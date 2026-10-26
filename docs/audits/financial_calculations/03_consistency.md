> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Phase 3: Concept Consistency Audit

This file accumulates across **P3-a, P3-b, P3-c, P3-d, P3-watchlist, and P3-reconcile**. It is gated
complete only by **P3-reconcile**. Each session continues the Finding-ID sequence without collision;
no session edits another session's findings or the P2/P1/priors content.

**Finding IDs used: F-001..F-056.** P3-a wrote F-001..F-012 (balance/cash-flow family). P3-b wrote
F-013..F-026 (loan/debt family). P3-c wrote F-027..F-031 (effective_amount sweep / transfer_amount /
transfer_amount_computed / Invariant 5). P3-d1 wrote F-032..F-040 (income/tax family:
`paycheck_gross`, `paycheck_net`, `taxable_income`, `federal_tax`, `state_tax`, `fica`,
`pre_tax_deduction`, `post_tax_deduction` mapped F-032..F-039 1:1, plus the standalone governed
legacy `calculate_federal_tax` dead-code finding F-040). P3-d2 (FINAL family session) wrote
F-041..F-056 (growth/retirement/savings/year-end family + the two Gate-A orphans:
`apy_interest`->F-041, `growth`->F-042, `employer_contribution`->F-043,
`contribution_limit_remaining`->F-044, `ytd_contributions`->F-045, `goal_progress`->F-046,
`emergency_fund_coverage_months`->F-047, `cash_runway_days`->F-048, `pension_benefit_annual`->F-049,
`pension_benefit_monthly`->F-050, `year_summary_jan1_balance`->F-051,
`year_summary_dec31_balance`->F-052, `year_summary_principal_paid`->F-053,
`year_summary_growth`->F-054, `year_summary_employer_total`->F-055, and
`entry_sum_total`+`entry_remaining`->F-056), and owns the Phase-3 family-coverage tally (all P3
sessions). P3-watchlist and P3-reconcile do not collide with this range.

P3-a scope: the balance / cash-flow family only -- `checking_balance`, `projected_end_balance`,
`account_balance`, `period_subtotal`, `chart_balance_series`, `net_worth`, `savings_total`,
`debt_total`. Loan, income, tax, savings, growth, transfer, and `effective_amount` concepts are
P3-b..d and are NOT audited here. The plan-vs-code watchlist is P3-watchlist. Phase 3 completion is
P3-reconcile's gate.

Read-only audit (audit plan section 0, hard rules). No source/test/migration file was modified.
Every divergence bullet was Read at the cited `file:line` in this session, not inferred from the P2
catalog. Where a verdict turns on a developer answer to a still-pending question (Q-08..Q-15 are
"A-NN proposed, pending confirmation"; Q-10/Q-11/Q-15 are PRIMARY-PATH UNKNOWN), the verdict is
UNKNOWN and the blocking Q-NN is named -- no guessed verdict (hard rule 5).

## E1 balance-row reconciliation (verification a)

E1 (`02_concepts.md:3215-3258`) balance-family rows: `account_balance`, `checking_balance`,
`projected_end_balance`, `period_subtotal`, `chart_balance_series`, `net_worth`, `savings_total`,
`debt_total` = **8 rows**. Findings produced for them: F-001..F-008 = **8**. Reconciled 1:1 (map
below). The five audit-plan section-3.1 mandatory comparisons each additionally get their own
finding even where AGREE: #1 -> F-009, #2 -> F-001 (account_balance is itself the 3-way
grid/`/accounts`/`/savings` comparison), #3 -> F-010, #4 -> F-011, #5 -> F-012.

| E1 balance row | Finding |
| --- | --- |
| `account_balance` | F-001 (also satisfies 3.1 #2) |
| `checking_balance` | F-002 |
| `projected_end_balance` | F-003 |
| `period_subtotal` | F-004 |
| `chart_balance_series` | F-005 |
| `net_worth` | F-006 |
| `savings_total` | F-007 |
| `debt_total` | F-008 |

No balance-family token appears in E1's "single-path verify only" list (`02_concepts.md:3259-3271`).
`cash_runway_days` is the only cash-flow-adjacent single-path token; P2-a explicitly deferred it to
the cash-flow/savings family (`02_concepts.md:28-36`), so it is owned by P3-b/c, not skipped here.
Zero E1 balance rows skipped.

### Status / effective-amount substrate (read once, cited by every finding below)

`Transaction.effective_amount` (`app/models/transaction.py:221-245`), Read at source: tier 1
`is_deleted -> Decimal("0")` (`:238-239`); tier 2 `status.excludes_from_balance -> Decimal("0")`
(`:240-241`); tier 3 `actual_amount if actual_amount is not None` (`:245`); tier 4 fallback
`estimated_amount` (`:245`). For a
**Projected expense with `actual_amount` NULL, `effective_amount == estimated_amount`.**

Status flag matrix, Read at `app/ref_seeds.py:79-84`:

| Status | is_settled | excludes_from_balance |
| --- | --- | --- |
| Projected | False | False |
| Paid (DONE) | True | False |
| Received | True | False |
| Credit | False | **True** |
| Cancelled | False | **True** |
| Settled | True | False |

The balance engine's projected-sum path (`_sum_remaining`/`_sum_all`) performs **no `.quantize()`**
-- it sums pre-quantized `Numeric(12,2)` operands. Rounding mode is therefore NOT the divergence
axis for the balance-family core; the A-01 verification verdict (`09_open_questions.md:37-62`,
PARTIALLY ACCURATE) is cited only where a balance consumer quantizes (F-008 PITI). The divergence
driver across the family is the
**`selectinload(Transaction.entries)` input difference feeding `_entry_aware_amount`**, proven
below.

### Entries-load matrix (the cross-family input divergence, Read at each site)

`_entry_aware_amount` (`app/services/balance_calculator.py:292`) returns `txn.effective_amount`
unchanged when `'entries' not in txn.__dict__` (`:353-354`) and otherwise (Projected expense,
entries non-empty) returns `max(estimated_amount - cleared_debit - sum_credit, uncleared_debit)`
(`:383-386`). Whether `entries` is in `__dict__` is decided entirely by whether the
**caller's query did `selectinload(Transaction.entries)`**:

| Balance path | `selectinload(entries)`? | `_entry_aware_amount` for a Projected envelope expense |
| --- | --- | --- |
| `grid.index` | YES `grid.py:229` | entry formula `max(est-cleared-credit, uncleared)` |
| `grid.balance_row` | YES `grid.py:438` | entry formula |
| dashboard `_compute_balances` | YES `dashboard_service.py:689` | entry formula |
| `/savings` `compute_dashboard_data` | **NO** `savings_dashboard_service.py:92-100` | falls back to `effective_amount` (= `estimated_amount`) |
| `/accounts` `checking_detail` | **NO** `accounts.py:1407-1416` | falls back to `effective_amount` |
| net-worth `_get_account_balance_map` | **NO** `year_end_summary_service.py:2085-2094` | falls back to `effective_amount` |

This single table is the structural root of reported symptom #1 and symptom #5. It is referenced by
F-001, F-002, F-003, F-005, F-006, F-007, F-009.

---

## Finding F-001: account_balance consistency

- Concept: `account_balance`
- Symptom link: #5 (`/accounts` vs other pages); contributes to #1
- Paths compared (every pair enumerated):
  - A = grid checking via `calculate_balances` (`grid.py:393` balance_row / `:164` index)
  - B = `/accounts` checking via `calculate_balances` (`accounts.py:1425`)
  - C = `/savings` via `_compute_account_projections` -> `calculate_balances`
    (`savings_dashboard_service.py:343`)
  - D = dashboard via `_compute_balances` -> `calculate_balances` (`dashboard_service.py:699`)
  - E = net-worth per-account via `_get_account_balance_map` (`year_end_summary_service.py:2127`)
  - F = loan account: `_compute_account_projections` `proj.current_balance`
    (`savings_dashboard_service.py:373`) vs net-worth `_schedule_to_period_balance_map`
    (`year_end_summary_service.py:2079-2081`) vs dashboard label `params.current_principal`
    (`loan/dashboard.html:104`)
  - Pairs: A-B, A-C, A-D, A-E, B-C, B-D, B-E, C-D, C-E, D-E, plus the 3-way loan sub-comparison F.
- Path A: `app/routes/grid.py:226-248` -- query scoped (`account_id` only `if account`, `:224-225`),
  `selectinload(entries)` `:229`, anchor `account.current_anchor_balance if account else 0.00`
  `:238`, `calculate_balances` `:243`.
- Path B: `app/routes/accounts.py:1407-1432` -- query `account_id`/`scenario_id`/`is_deleted`,
  **no `selectinload(entries)`**, anchor `account.current_anchor_balance or 0.00` `:1418`,
  `current_bal = balances.get(current_period.id)` `:1432`.
- Path C: `app/services/savings_dashboard_service.py:320-352` -- `acct_transactions` filtered in
  Python from a preload with **no `selectinload(entries)`** (`:92-100`), anchor
  `acct.current_anchor_balance or 0.00` `:325`, `current_bal = balances.get(current_period.id)`
  `:352`.
- Path D: `app/services/dashboard_service.py:687-705` -- `selectinload(entries)` `:689`, anchor raw
  `account.current_anchor_balance` (may be None) `:700`, returns None when
  `current_anchor_period_id is None` `:683-684`.
- Path E: `app/services/year_end_summary_service.py:2065-2128` -- returns None when
  `current_anchor_period_id is None` `:2065-2066`, loan branch uses the amortization schedule
  `:2079-2081`, **no `selectinload(entries)`** `:2085-2094`, anchor
  `account.current_anchor_balance or ZERO` `:2096`.
- Compared dimensions:
  - Status filter: identical for A-E (engine `_sum_remaining`/`_sum_all` gate
    `status_id != projected_id`, `balance_calculator.py:411-412,443-444`). Loan branch E differs
    (schedule, no status filter).
  - Transaction-type filter: identical (engine routes income via `is_income`, expense via
    `is_expense`, `:414-417,446-449`); shadow transactions included identically (no `transfer_id`
    filter in the engine).
  - Effective-amount logic: **DIVERGES.** A and D pass entries-loaded txns -> `_entry_aware_amount`
    entry formula (`:383-386`). B, C, E pass entries-unloaded txns -> `_entry_aware_amount` returns
    `effective_amount` (`:353-354`). Same engine, different per-expense contribution.
  - Period scope: all paths use the user's full `all_periods` set anchor-forward. AGREE.
  - Anchor handling: **DIVERGES on the anchor-None case.** A:
    `current_anchor_period_id if account else current_period.id` (`grid.py:239-241`) -> passes None
    when the column is None -> engine matches no anchor period -> `balances` empty -> grid renders a
    blank row. B/C: `... or (current_period.id ...)` (`accounts.py:1419-1421`,
    `savings_dashboard_service.py:326-328`) -> falls back to `current_period.id` with a $0.00 anchor
    -> a populated projection. D/E: `return None` when `current_anchor_period_id is None`
    (`dashboard_service.py:683-684`, `year_end_summary_service.py:2065-2066`) -> the account is
    omitted entirely. Four different behaviors for one missing-anchor input.
  - Scenario filter: all baseline (`get_baseline_scenario`, `grid.py:177`, `accounts.py:1400`,
    `savings_dashboard_service.py:85`, `dashboard_service` via caller,
    `year_end_summary_service._get_account_balance_map` `scenario.id` `:2089`). AGREE.
  - is_deleted handling: all paths `Transaction.is_deleted.is_(False)` in the query (`grid.py:222`,
    `accounts.py:1413`, `savings_dashboard_service.py:97`, `dashboard_service.py:694`,
    `year_end_summary_service.py:2091`). AGREE.
  - Quantization: none in the projected-sum path (pre-quantized operands). AGREE.
  - Source-of-truth column read: A-D checking -> computed from anchor + Projected txns. E loan ->
    amortization schedule (`_schedule_to_period_balance_map` `:2079-2081`). F loan:
    `proj.current_balance` (engine, A-04) vs schedule vs stored `params.current_principal`
    (`loan/dashboard.html:104`). Three different bases for one loan's displayed balance.
- Divergences:
  - Expense formula: A/D use `_entry_aware_amount` entry math (`balance_calculator.py:383-386`,
    entries loaded at `grid.py:229`, `dashboard_service.py:689`); B/C/E use `effective_amount`
    (entries NOT loaded at `accounts.py:1407-1416`, `savings_dashboard_service.py:92-100`,
    `year_end_summary_service.py:2085-2094`). SILENT.
  - Anchor-None: four behaviors (blank row / `$0`-anchored projection / account omitted), cited
    above. SCOPE.
  - Loan-balance base: `savings_dashboard_service.py:373` (`proj.current_balance`) vs
    `year_end_summary_service.py:2079-2081` (schedule) vs `loan/dashboard.html:104`
    (`params.current_principal`, stored). SOURCE.
  - Grid account scoping: `grid.py:224-225` applies `account_id` filter only `if account`; B/C/D/E
    always scope by `account_id`. If `resolve_grid_account` returns None (user with no resolvable
    grid account), grid sums every account's transactions while the others do not. SCOPE
    (low-incidence).
  - Per-account dispatch is implemented twice: `_compute_account_projections`
    (`savings_dashboard_service.py:294`, drives C and the `/savings` + dashboard cards) and
    `_get_account_balance_map` (`year_end_summary_service.py:2036`, drives E and net worth). No code
    designates either canonical.
- Risk: For any checking account that has at least one Projected envelope expense with cleared or
  credit entries, the same `(user, period, scenario, account)` yields a higher balance on the grid
  and dashboard (entry formula holds back only the unreconciled remainder) than on `/savings`,
  `/accounts`, and the net-worth input (which hold back the full `estimated_amount`). Worked example
  (from the `_entry_aware_amount` docstring grocery case, `balance_calculator.py:329-333`): anchor
  `$1,000.00`; one Projected expense `estimated_amount = $500.00`, `actual_amount` NULL, three
  cleared debit entries summing `$462.34`, no credit/uncleared entries. Grid/dashboard: expense
  contribution `= max(500.00 - 462.34 - 0, 0) = $37.66`; current-period balance
  `1000.00 - 37.66 = $962.34`. `/savings` and `/accounts`: entries not loaded, contribution
  `= effective_amount = estimated_amount = $500.00`; balance `1000.00 - 500.00 = $500.00`. One
  account, one period, two displayed balances `$962.34` vs `$500.00`, no error raised. Net worth
  consumes the `$500.00`-style figure and the loan liability uses a third base again.
- Verdict: DIVERGE (checking-account and anchor-None axes hold independently of any developer
  answer). The "which per-account dispatcher is canonical" axis is **UNKNOWN, blocked on Q-15**.
- If DIVERGE: classification: SILENT_DRIFT (entries-load expense divergence), SCOPE_DRIFT
  (anchor-None handling; grid account-scope), SOURCE_DRIFT (loan base: stored vs engine vs
  schedule), PLAN_DRIFT (dual per-account dispatch; net_worth_amort W-152 `planned-per-plan`).
- Open questions for the developer: Q-15 (`09_open_questions.md:621-658`; canonical multi-account
  aggregate balance owner -- resolves the dispatcher and loan-base axes), Q-11
  (`09_open_questions.md:423-457`; which principal the loan card must show). New question raised:
  **Q-16** (anchor-None handling divergence; filed in `09_open_questions.md`).

---

## Finding F-002: checking_balance consistency

- Concept: `checking_balance`
- Symptom link: **#1** (grid `$160` vs `/savings` `$114.29`)
- Paths compared: Pair A (grid vs `/accounts`), Pair B (grid vs `/savings`), Pair C (grid
  subtotal vs grid balance row -- same page).
- Path A1 grid: `app/routes/grid.py:243` `calculate_balances`, entries loaded `:229`.
- Path A2 `/accounts`: `app/routes/accounts.py:1425` `calculate_balances`, entries NOT loaded
  `:1407-1416`.
- Path B `/savings`: `app/services/savings_dashboard_service.py:343` `calculate_balances` via
  `_compute_account_projections`, entries NOT loaded `:92-100`.
- Path C: grid subtotal `app/routes/grid.py:263-279` (expense via raw `txn.effective_amount` `:274`)
  vs grid balance row `_sum_remaining`/`_sum_all` `app/services/balance_calculator.py:417,449`
  (expense via `_entry_aware_amount`).
- Compared dimensions:
  - Status filter: all three Projected-only. Grid balance: `status_id != projected_id`
    (`balance_calculator.py:411-412,443-444`). `/accounts`, `/savings`: same engine, same gate. Grid
    subtotal: `if txn.is_deleted or txn.status_id != projected_id: continue` (`grid.py:269`). AGREE
    -- Projected-only is uniform; A-02/W-091/W-092 settled-source exclusion holds (verified at the
    gate).
  - Transaction-type filter: AGREE (income `is_income`, expense `is_expense`; subtotal `:271-274`).
  - Effective-amount logic: **DIVERGES.** Grid balance row: `_entry_aware_amount` with entries
    loaded -> entry formula. `/accounts` and `/savings` balance: `_entry_aware_amount` falls back to
    `effective_amount` (entries not loaded). Grid subtotal: raw `txn.effective_amount`
    (`grid.py:274`) -- NOT `_entry_aware_amount`, even though entries ARE loaded on that same
    request.
  - Period scope: all `all_periods` anchor-forward (subtotal restricted to visible `periods`
    `grid.py:265`, a display window only). AGREE for the balance.
  - Anchor handling: grid `... if account else ...` (`grid.py:238-241`); `/accounts`,`/savings`
    `... or ...` (`accounts.py:1418-1421`, `savings_dashboard_service.py:325-328`) -- diverges only
    on the anchor-None case (see F-001 / Q-16).
  - Scenario filter: all baseline. AGREE.
  - is_deleted handling: all exclude `is_deleted` (query level; subtotal also re-checks
    `txn.is_deleted` `:269`). AGREE.
  - Quantization: none (pre-quantized). AGREE.
  - Source-of-truth column read: all computed from anchor + Projected txns; none read a stored
    balance column. AGREE.
- Divergences:
  - Pair A (grid vs `/accounts`): grid `_entry_aware_amount` entry formula
    (`balance_calculator.py:383-386`, entries `grid.py:229`) vs `/accounts` `effective_amount`
    (entries absent, `accounts.py:1407-1416` has no `selectinload`). SILENT.
  - Pair B (grid vs `/savings`): identical mechanism -- `/savings` preload
    `savings_dashboard_service.py:92-100` has no `selectinload(entries)`; grid `:229` does. SILENT.
  - Pair C (same page): grid subtotal row uses raw `effective_amount` (`grid.py:274`) while the grid
    balance row uses `_entry_aware_amount`; for a Projected envelope expense with cleared entries
    the on-screen subtotal and the on-screen balance disagree by `cleared_debit + sum_credit`.
    SILENT (Q-10 governs whether subtotal is canonicalized; see F-004).
- Risk: Same worked example as F-001. Concretely for symptom #1: grid shows the entry-aware
  current-period balance (holds back only the unreconciled budget remainder); `/savings` shows
  the full-estimate balance. Grid value is the larger of the two whenever cleared/credit entries
  exist on Projected expenses. The reported `$160` (grid) vs `$114.29` (`/savings`) is consistent
  with this sign and mechanism: the `$45.71` gap equals the sum, over the period's Projected
  envelope expenses, of `estimated_amount - max(estimated_amount - cleared_debit - sum_credit,
  uncleared_debit)` -- the cleared/credit entry value already reflected in the anchor that
  `/savings` double-holds-back. The exact transaction set producing `$45.71` cannot be pinned
  without the developer's data, but the dimension is pinned: it is the entries-`selectinload`
  input difference at `grid.py:229` vs `savings_dashboard_service.py:92-100`, not the engine,
  not the status filter, not the scenario, not the anchor (Phase 5 owns the per-account number
  reconstruction).
- Verdict: DIVERGE
- If DIVERGE: classification: SILENT_DRIFT (entries-load expense divergence across pages and the
  subtotal-vs-balance same-page divergence).
- Open questions for the developer: cross-link Q-10 (`09_open_questions.md:373-421`; Pair C
  subtotal canonicalization), Q-16 (anchor-None, raised in F-001). The entries-load divergence
  itself is unambiguous (no new Q): the developer expectation E-04 (`00_priors.md:178-182`) makes
  the unlabeled difference a finding by definition.

---

## Finding F-003: projected_end_balance consistency

- Concept: `projected_end_balance`
- Symptom link: **#1** and **#5** (E-04 invariant anchor)
- Paths compared: A grid (`calculate_balances`, `grid.py:243`/`:446`); B `/accounts`
  (`accounts.py:1425`); C `/savings` (`savings_dashboard_service.py:343`/`:335`); D dashboard
  (`dashboard_service.py:699`); E loan account end-of-period: `_compute_account_projections`
  `proj.schedule` rows (`savings_dashboard_service.py:384-387`) vs net-worth schedule
  (`year_end_summary_service.py:2079-2081`) vs stored card `loan/dashboard.html:104`. Pairs
  A-B, A-C, A-D, B-C, B-D, C-D (checking/HYSA) and the E 3-way (loan).
- Path A: `app/routes/grid.py:243-248` -- `calculate_balances`, entries loaded `:229`.
- Path B: `app/routes/accounts.py:1425-1450` -- `calculate_balances`; 3/6/12-month horizon walk
  `:1445-1451`; entries not loaded.
- Path C: `app/services/savings_dashboard_service.py:330-400` --
  `calculate_balances`/`_with_interest`; horizon walk `:394-400`; loan horizon from `proj.schedule`
  `:379-387`; entries not loaded.
- Path D: `app/services/dashboard_service.py:699` -- `calculate_balances`, entries loaded `:689`.
- Compared dimensions:
  - Status filter / type filter / scenario / is_deleted / quantization: identical to F-002 (same
    engine, same baseline, same `is_deleted` query gate, no quantize). AGREE.
  - Effective-amount logic: **DIVERGES** identically to F-002 -- A and D entry-aware; B and C fall
    back to `effective_amount`.
  - Period scope: checking/HYSA all `all_periods`. The horizon projections differ in stride:
    `/accounts` and `/savings` non-loan walk by period-index offset `[6, 13, 26]`
    (`accounts.py:1445`, `savings_dashboard_service.py:394`) while the loan branch walks
    `proj.schedule` by `[3, 6, 12]` month offsets (`savings_dashboard_service.py:379-383`). For a
    checking account the `[6,13,26]`-period stride is consistent between B and C. SCOPE note (loan
    vs non-loan stride is by design per account type).
  - Anchor handling: as F-001/F-002 (anchor-None divergence, Q-16).
  - Source-of-truth column read: checking computed; loan E: `proj.current_balance` (A-04 dual
    policy) vs schedule vs stored `params.current_principal`. The loan dashboard renders the
    **stored** column directly (`loan/dashboard.html:104`,
    `${{ ... params.current_principal ... }}`) while `/savings` consumes `proj.current_balance`
    (`savings_dashboard_service.py:373`) and net worth consumes the schedule
    (`year_end_summary_service.py:2079-2081`). Three bases.
- Divergences:
  - Checking/HYSA: entries-load expense divergence (A/D vs B/C), cited as in F-002. SILENT.
  - Loan account end-of-period balance: stored vs engine vs schedule (`loan/dashboard.html:104` vs
    `savings_dashboard_service.py:373` vs `year_end_summary_service.py:2079-2081`). SOURCE.
  - Anchor-None: as F-001 (Q-16). SCOPE.
- Risk: Checking flavor identical to F-002 (same `$962.34` vs `$500.00` style split). Loan
  flavor: for a fixed-rate loan with confirmed payments, `proj.current_balance` (engine-walked)
  != stored `params.current_principal`; the `/savings` projected balance and net-worth liability
  move with payments while the `/accounts/<id>/loan` card stays at the static stored value, so
  E-04 ("same number on every page") fails for the loan card vs `/savings`. ARM loans coincide
  only because `amortization_engine` assigns `current_balance = current_principal` for ARM
  (A-04). This is symptom #5's loan facet and symptom #3's display facet (loan detail is P2-b;
  recorded here because `projected_end_balance` of a loan account is what `/savings` and net
  worth consume).
- Verdict: DIVERGE (checking entries-load axis unconditionally; loan-base axis is real but its
  canonicalization is **UNKNOWN, blocked on Q-11 / Q-15**).
- If DIVERGE: classification: SILENT_DRIFT (checking entries-load), SOURCE_DRIFT (loan base),
  SCOPE_DRIFT (anchor-None).
- Open questions for the developer: Q-11 (`09_open_questions.md:423-457`), Q-15
  (`09_open_questions.md:621-658`), Q-16 (anchor-None, raised in F-001).

---

## Finding F-004: period_subtotal consistency

- Concept: `period_subtotal`
- Symptom link: #1 (Pair C of F-002 is the same-page subtotal-vs-balance divergence)
- Paths compared:
  - D1 grid subtotal: `app/routes/grid.py:263-279` -- Projected-only, expense via raw
    `txn.effective_amount` `:274`.
  - D2 balance-calc internal subtotal: `_sum_remaining`/`_sum_all`
    `app/services/balance_calculator.py:389-419,422-451` -- Projected-only, expense via
    `_entry_aware_amount` `:417,449`.
  - D3 dashboard spending comparison: `_sum_settled_expenses`
    `app/services/dashboard_service.py:607-633` -- DONE/RECEIVED/SETTLED only `:613-617`,
    expense-only `:628`, `abs(effective_amount)` `:633`.
  - Pairs: D1-D2 (same page), D1-D3, D2-D3.
- Path D1: `app/routes/grid.py:263-279` Read at source --
  `projected_id = ref_cache.status_id(StatusEnum.PROJECTED)` `:263`; gate
  `if txn.is_deleted or txn.status_id != projected_id: continue` `:269`;
  `income += txn.effective_amount` `:272`; `expense += txn.effective_amount` `:274`;
  `net = income - expense` `:278`.
- Path D2: `app/services/balance_calculator.py:408-419` -- gate `:411-412`;
  `income += txn.effective_amount` `:415`; `expenses += _entry_aware_amount(txn)` `:417`.
- Path D3: `app/services/dashboard_service.py:620-633` -- status filter
  `Transaction.status_id.in_(settled_ids)` `:627`;
  `return sum(abs(txn.effective_amount) for txn in txns)` `:633`.
- Compared dimensions:
  - Status filter: D1 Projected-only (`grid.py:269`); D2 Projected-only
    (`balance_calculator.py:411-412`); D3 **DONE/RECEIVED/SETTLED only**
    (`dashboard_service.py:613-617,627`). D1/D2 vs D3 are disjoint status sets -- cannot agree by
    construction.
  - Transaction-type filter: D1 income+expense split; D2 income+expense split; D3 expense-only.
  - Effective-amount logic: D1 raw `effective_amount`; D2 `_entry_aware_amount`; D3
    `abs(effective_amount)`. D1 vs D2 diverge for a Projected envelope expense with cleared entries
    even though D1 and D2 read the SAME loaded transaction objects on the grid request.
  - Period scope: D1 visible `periods` window (`grid.py:265`); D2 per period inside the
    anchor-forward walk; D3 a single period vs its prior period (`dashboard_service.py:570,581`).
  - Anchor / scenario / is_deleted: scenario baseline for all; D1 re-checks `txn.is_deleted` `:269`;
    not anchor-relative (subtotal is a per-period sum, not a running balance).
  - Quantization: none in D1/D2; D3 sums `abs()` of pre-quantized values. AGREE (no rounding drift).
  - Source-of-truth column read: all read transaction amounts via
    `effective_amount`/`_entry_aware_amount`; no stored subtotal column exists (A-10).
- Divergences:
  - D1-vs-D2 (same page): grid renders the D1 subtotal row (`grid.py:274`, raw `effective_amount`)
    and the D2-derived balance row (`balance_calculator.py:417`, `_entry_aware_amount`) from the
    same `all_transactions`; for a Projected envelope expense with `cleared_debit`/`sum_credit > 0`
    they disagree by exactly `cleared_debit + sum_credit` (the amount `_entry_aware_amount` removes
    via `max(estimated - cleared - credit, uncleared)`, `:383-386`). SILENT.
  - D1-vs-D3: opposite status sets (`grid.py:269` Projected vs `dashboard_service.py:627` settled).
    Disjoint by construction -- they measure different quantities. DEFINITION.
  - D2-vs-D3: same as D1-D3 (status disjoint) plus expense-formula difference. DEFINITION.
- Risk: A user comparing the grid's per-period "net" subtotal to the change in the grid's
  balance row across that period sees two numbers that should reconcile but do not, whenever a
  Projected envelope expense in the period has cleared entries. Worked example: period with one
  Projected expense `estimated_amount = $500.00`, cleared debit entries `$462.34`. Grid subtotal
  expense (`grid.py:274`) `= $500.00`; the balance row's period delta uses `_entry_aware_amount`
  `= $37.66`. The subtotal row says the period cost `$500.00`; the balance row drops by
  `$37.66`. The dashboard spending-comparison for the same period reports `$0.00` for this
  expense entirely (it is not settled, excluded by `dashboard_service.py:627`).
- Verdict: **UNKNOWN** -- `period_subtotal` is PRIMARY PATH UNKNOWN, blocked on **Q-10**
  (`09_open_questions.md:373-421`: is the per-period subtotal a grid display detail or a shared
  financial concept; if shared, is the canonical expense `effective_amount` or
  `_entry_aware_amount`). The D1-D2 and D1-D3 divergences above are recorded as facts regardless
  of the answer (per A-10); the verdict label is gated because "is this a finding or intended"
  is exactly what Q-10 decides.
- If DIVERGE (conditional on Q-10): D1-D2 -> SILENT_DRIFT; D1-D3/D2-D3 -> DEFINITION_DRIFT.
- Open questions for the developer: Q-10 (governing; resolves the verdict). Q-12
  (`09_open_questions.md:459-512`) governs the obligations-path subtotal (out of P3-a balance
  scope; cross-link only).

---

## Finding F-005: chart_balance_series consistency

- Concept: `chart_balance_series`
- Symptom link: none directly (inherits F-002 mechanism for the HYSA flavor)
- Paths compared (balance-family flavor only; loan/investment chart series are P3-b/d):
  - A = HYSA/checking series via `calculate_balances_with_interest` consumed by `grid.balance_row`
    (`app/services/balance_calculator.py:112`, entries loaded `grid.py:438`).
  - B = the same engine's series consumed by `accounts.checking_detail`/`interest_detail`
    (`accounts.py:1425`, entries NOT loaded `:1407-1416`).
  - Pair: A-B. (Loan series `amortization_engine.generate_schedule`/`get_loan_projection`,
    investment `growth_engine.project_balance`, debt-strategy `calculate_strategy` are per-domain
    engines owned by P3-b/d; noted, not audited here.)
- Path A: `app/services/balance_calculator.py:112-173` -- delegates base to `calculate_balances`
  `:135`, layers per-period interest `:161-171`; consumed with entries loaded (`grid.py:438`).
- Path B: `app/routes/accounts.py:1425` -- same engine family, entries not loaded.
- Compared dimensions:
  - Status / type / scenario / is_deleted / period scope: identical (same engine, baseline, full
    periods). AGREE.
  - Effective-amount logic: **DIVERGES** -- the interest wrapper's base balances come from
    `calculate_balances` (`balance_calculator.py:135`), so the F-002 entries-load divergence
    propagates into the charted series exactly as into the scalar balance.
  - Quantization: `calculate_balances_with_interest` adds `calculate_interest` output per period
    (`:161-170`); interest rounding is owned by `interest_projection` (P3-d / `apy_interest`), not
    the balance axis. No quantize in the base-balance roll. AGREE on the balance axis.
  - Source-of-truth column read: computed; no stored series column. AGREE.
- Divergences:
  - The charted series and a non-chart page showing the same account's balance disagree by the F-002
    entries-load delta whenever the chart path loaded entries and the page path did not (or vice
    versa). `grid.balance_row` loads entries (`grid.py:438`); `accounts.checking_detail` does not
    (`accounts.py:1407-1416`). SILENT (inherits F-002).
  - Tooling note (grep-verified): `app/services/chart_data_service.py` no longer exists -- removed
    in commit `e3b3a5e` ("chore(cleanup): remove old /charts page, templates, JS, and
    chart_data_service"); only a stale `__pycache__/chart_data_service.cpython-314.pyc` remains and
    `app/routes/charts.py:20 def dashboard()` carries no balance logic. Audit-plan section 3.1's
    "chart data service" referent is dead code; the live chart-series producers are the per-domain
    engines.
- Risk: Same `$962.34` vs `$500.00`-style split as F-002, manifested as a chart line that does
  not match the scalar balance card for the same account/period when one path selectinloaded
  entries and the other did not.
- Verdict: DIVERGE (HYSA/checking flavor, inherits F-002).
- If DIVERGE: classification: SILENT_DRIFT.
- Open questions for the developer: none new (inherits F-002 / Q-16; loan/investment chart
  series deferred to P3-b/d). E-17 (JS treats series display-only) is a coding-standards check
  owned by P3-watchlist, not re-litigated here.

---

## Finding F-006: net_worth consistency

- Concept: `net_worth`
- Symptom link: #5 (net-worth liability vs `/savings` debt card; investment Dec-31 equality)
- Paths compared: single token producer `_compute_net_worth` (`year_end_summary_service.py:689`),
  so the comparison is on its **inputs** -- the `_get_account_balance_map` dispatch
  (`year_end_summary_service.py:2036`) vs the parallel `_compute_account_projections` dispatch
  (`savings_dashboard_service.py:294`) that drives `/savings`/dashboard. Sub-pairs: (i) loan
  branch: `_get_account_balance_map` schedule (`year_end_summary_service.py:2079-2081`) vs
  `_compute_account_projections` `proj.current_balance` (`savings_dashboard_service.py:373`);
  (ii) investment branch: `_build_investment_balance_map` (`year_end_summary_service.py:2121`)
  vs `_project_investment_for_year` (`year_end_summary_service.py:938`, the savings-progress
  path) -- W-159 Dec-31 equality; (iii) checking/HYSA branch: both route through
  `calculate_balances`/`_with_interest` but with the entries-load difference of F-001.
- Path A: `app/services/year_end_summary_service.py:723-747` (`_compute_net_worth` ->
  `_build_account_data` `:727` -> `_get_account_balance_map` `:774`), liabilities negated via
  `is_liability` `:782-785`, `delta = dec31 - jan1` `:746`.
- Path B: `app/services/savings_dashboard_service.py:294-373` (`_compute_account_projections`).
- Compared dimensions:
  - Status / type / scenario / is_deleted: checking/HYSA branch identical engine and gates; baseline
    scenario both (`year_end_summary_service.py:2089`, `savings_dashboard_service.py:96`);
    `is_deleted` excluded both (`:2091`, `:97`). AGREE on those.
  - Effective-amount logic: **DIVERGES** -- `_get_account_balance_map` does not
    `selectinload(entries)` (`:2085-2094`); neither does the `/savings` preload
    (`savings_dashboard_service.py:92-100`). Both fall back to `effective_amount`, so net worth and
    the `/savings` card AGREE with each other on the expense formula but BOTH DIVERGE from the
    grid/dashboard entry-aware figure (F-001 row E vs A/D).
  - Period scope: net worth samples 12 month-end periods (`year_end_summary_service.py:726,737`);
    `/savings` uses the current period and 3/6/12 horizons. Different sampling of the same
    anchor-forward series (by design).
  - Anchor handling: `_get_account_balance_map` returns None when `current_anchor_period_id is None`
    (`:2065-2066`) -> account dropped from net worth; `_compute_account_projections` falls back to
    `current_period.id` (`savings_dashboard_service.py:326-328`). DIVERGES (Q-16).
  - Source-of-truth column read: loan -> net worth uses the amortization **schedule**
    (`:2079-2081`); `/savings` uses `proj.current_balance` (`savings_dashboard_service.py:373`).
    Different bases (A-04).
  - Quantization: none in the roll. AGREE.
- Divergences:
  - Loan liability base: schedule (`year_end_summary_service.py:2079-2081`) vs
    `proj.current_balance` (`savings_dashboard_service.py:373`); for a fixed-rate loan with
    confirmed payments these differ (A-04). SOURCE.
  - Investment Dec-31: `_build_investment_balance_map` (`:2121`) vs `_project_investment_for_year`
    (`:938`) -- two investment projection functions; net_worth_amort W-159 requires equality and is
    `planned-per-plan` (not implemented). PLAN.
  - Dual dispatch: `_get_account_balance_map` (`:2036`) and `_compute_account_projections`
    (`savings_dashboard_service.py:294`) reimplement the same per-account type dispatch.
    net_worth_amort W-152 ("identical calculation paths") is `planned-per-plan`. PLAN.
  - Anchor-None drops the account from net worth (`:2065-2066`) but not from `/savings`. SCOPE.
- Risk: For a user with a fixed-rate mortgage that has confirmed payments, the net-worth
  liability uses the schedule-derived balance while the `/savings` debt card uses
  `proj.current_balance`; for an investment account the Dec-31 net-worth value uses
  `_build_investment_balance_map` while the year-end savings-progress section uses
  `_project_investment_for_year` -- W-159 demands these be equal and there is no code enforcing
  it. Net worth therefore can show a different total than the sum of the figures the user sees
  on `/savings` for the same accounts and date.
- Verdict: **UNKNOWN** -- blocked on **Q-15** (`09_open_questions.md:621-658`: which dispatcher
  is canonical; whether net_worth_amort W-152/W-159 is "code must catch up" PLAN_DRIFT or "plan
  superseded"). The concrete SOURCE/SCOPE divergences are recorded regardless; the verdict label
  is gated because the plan-vs-code direction is the developer's call (audit plan section 9).
- If DIVERGE (conditional on Q-15): SOURCE_DRIFT (loan base), PLAN_DRIFT (dual dispatch, W-159
  investment equality), SCOPE_DRIFT (anchor-None).
- Open questions for the developer: Q-15 (governing), Q-11 (loan card base), Q-16 (anchor-None).

---

## Finding F-007: savings_total consistency

- Concept: `savings_total`
- Symptom link: #5 (savings aggregate differs across `/savings`, retirement gap, year-end)
- Paths compared (three independent aggregators):
  - A = `/savings` aggregate: `_compute_account_projections` (`savings_dashboard_service.py:294`)
    summed into `total_savings` (`savings_dashboard_service.py:142-145`, liquid accounts).
  - B = retirement-gap savings: `compute_gap_data` -> `_project_retirement_accounts`
    (`retirement_dashboard_service.py:79,338`).
  - C = year-end savings progress: `_compute_savings_progress` (`year_end_summary_service.py:887`).
  - Pairs: A-B, A-C, B-C; plus W-159: C Dec-31 investment vs net-worth (F-006) investment Dec-31.
- Path A: `app/services/savings_dashboard_service.py:142-145` --
  `total_savings += ad["current_balance"]` for `account_type.is_liquid`; `ad["current_balance"]` is
  `balances.get(current_period.id)` (`:352`) or `proj.current_balance` for loans (`:373`).
- Path B: `app/services/retirement_dashboard_service.py:79-168+` -- loads
  RETIREMENT/INVESTMENT-category accounts (`:151-168`) and projects via
  `_project_retirement_accounts` `:338` (growth-engine; P3-d owns the growth math).
- Path C: `app/services/year_end_summary_service.py:887-983` -- investment via
  `_project_investment_for_year` `:938`; interest via `_get_account_balance_map` `:944`; plain via
  `_get_account_balance_map` `:961`; `_lookup_balance_with_anchor_fallback` `:947-968`.
- Compared dimensions:
  - Account set: A = liquid accounts at the current period (`savings_dashboard_service.py:144`); B =
    RETIREMENT+INVESTMENT-category accounts (`retirement_dashboard_service.py:151-158`); C =
    non-debt non-checking savings accounts at Jan-1/Dec-31. Different account universes and as-of
    dates by design.
  - Effective-amount logic: A and C non-investment branches go through
    `calculate_balances`/`_get_account_balance_map` with NO `selectinload(entries)` ->
    `effective_amount` fallback (same as F-001 row C/E). Consistent between A and C; both DIVERGE
    from grid/dashboard entry-aware.
  - Period scope: A current period; C Jan-1/Dec-31 of the target year; B as-of planned retirement
    date. Different by design.
  - Anchor handling: A `... or current_period.id` (`savings_dashboard_service.py:326-328`); C via
    `_get_account_balance_map` returns None on anchor-None (`year_end_summary_service.py:2065-2066`)
    then `_lookup_balance_with_anchor_fallback`. DIVERGES on anchor-None (Q-16).
  - Source-of-truth / quantization: computed; no stored aggregate; no quantize in the roll.
- Divergences:
  - Three separate aggregators with no shared canonical (A `savings_dashboard_service.py:294`; B
    `retirement_dashboard_service.py:338`; C `year_end_summary_service.py:887`). PLAN/SOURCE.
  - W-159: C Dec-31 investment (`_project_investment_for_year` `:938`) vs net-worth investment
    Dec-31 (`_build_investment_balance_map` `year_end_summary_service.py:2121`) -- required equal,
    not enforced. PLAN.
  - Investment growth math (B/C) is P3-d (`growth`); cross-linked, not audited here.
- Risk: The "current savings" the user sees on `/savings`, the savings figure on the
  retirement-gap page, and the year-end savings-progress Dec-31 balance are produced by three
  different functions over three different account universes and dates. Where the universes
  overlap (e.g. a single liquid investment account), there is no code guaranteeing the numbers
  reconcile, and W-159's required Dec-31 equality between savings-progress and net-worth
  investment is not implemented.
- Verdict: **UNKNOWN** -- blocked on **Q-15** (canonical aggregator owner). Structural
  multi-aggregator divergence and the W-159 gap recorded regardless.
- If DIVERGE (conditional on Q-15): PLAN_DRIFT (multi-aggregator, W-159), SOURCE_DRIFT (entries
  fallback vs grid), SCOPE_DRIFT (anchor-None, account-universe).
- Open questions for the developer: Q-15 (governing), Q-16 (anchor-None). Growth-engine
  investment detail deferred to P3-d.

---

## Finding F-008: debt_total consistency

- Concept: `debt_total`
- Symptom link: #5 (debt card vs net-worth liability vs debt-strategy)
- Paths compared:
  - A = `/savings` + dashboard debt card: `_compute_debt_summary`
    (`savings_dashboard_service.py:802`), `total_debt` from stored `current_principal`.
  - B = net-worth loan liability: `_compute_net_worth` -> `_get_account_balance_map` loan branch
    (`year_end_summary_service.py:2079-2081`, amortization schedule).
  - C = year-end debt progress: `_compute_debt_progress` (`year_end_summary_service.py:824`,
    schedule-derived).
  - D = debt-strategy aggregate: `calculate_strategy` (`debt_strategy_service.py:521`) -- P3-b owns
    the strategy math; cross-link the per-loan base only.
  - Plus internal-consistency sub-check inside A. Pairs: A-B, A-C, A-D, B-C.
- Path A: `app/services/savings_dashboard_service.py:835-855` -- `lp = ad["loan_params"]` `:839`;
  `principal = Decimal(str(lp.current_principal))` `:840` (STORED column); skip `is_paid_off`
  `:836-837` and `principal <= 0` `:842-843`; `total_debt += principal` `:855`; PITI
  `(monthly_pi + monthly_escrow).quantize(_TWO_PLACES, ROUND_HALF_UP)` `:851-853`.
- Path B: `app/services/year_end_summary_service.py:2071-2081` -- loan balance =
  `_schedule_to_period_balance_map(debt_schedules[account.id], periods, original)` (amortization
  schedule), negated as liability in `_sum_net_worth_at_period`.
- Path C: `app/services/year_end_summary_service.py:865-871` -- `jan1_bal`/`dec31_bal` via
  `_balance_from_schedule_at_date` (schedule); `principal_paid = jan1_bal - dec31_bal` `:871`.
- Compared dimensions:
  - Source-of-truth column read: **DIVERGES.** A reads stored `LoanParams.current_principal`
    (`savings_dashboard_service.py:840`); B and C derive from the amortization schedule
    (`year_end_summary_service.py:2079-2081`, `:865-870`). Per A-04 the stored column and the
    schedule-walked balance differ for a fixed-rate loan with confirmed payments.
  - Status / type / scenario / is_deleted: A operates on `account_data` already filtered; B/C
    operate on debt schedules; not directly comparable on transaction filters (different inputs by
    design). The divergence is the base, not the filter.
  - Quantization: A quantizes PITI `ROUND_HALF_UP` (`savings_dashboard_service.py:851-853`) --
    A-01-clean per the A-01 verification verdict (`09_open_questions.md:37-62`; this site is NOT in
    the 24-omission list). `total_debt` itself (`:855`) is a sum of `Decimal(str(...))`, no
    quantize. AGREE on rounding.
  - Period scope: A as-of "now" (stored column); B/C Jan-1/Dec-31 schedule points. Different by
    design.
- Divergences:
  - A vs B/C base: stored `current_principal` (`savings_dashboard_service.py:840`) vs
    amortization-schedule balance (`year_end_summary_service.py:2079-2081`, `:865-870`).
    SOURCE/DEFINITION (A-04).
  - **Internal inconsistency inside A (holds regardless of Q-15):** `_compute_debt_summary`
    reads `ad["loan_params"]` and computes `total_debt` from `lp.current_principal`
    (`savings_dashboard_service.py:840,855`), but the same `ad` dict's `current_balance` was set
    to `proj.current_balance` (the A-04 engine value) at `savings_dashboard_service.py:373`. One
    service, one loan, two different principals: the debt card's `total_debt` (stored) and the
    account card's `current_balance` (engine) can disagree on the same page for the same loan.
    SOURCE_DRIFT.
  - D (debt-strategy) per-loan base is P3-b; flagged for the cross-comparison there.
- Risk: For a fixed-rate mortgage with confirmed payments, the `/savings` debt card shows the
  stored `current_principal` (which symptom #3 says does not move as transfers settle) while the
  net-worth liability and year-end debt-progress show the schedule-walked balance (which does
  move). On the same `/savings` page the account card can show the engine `current_balance`
  while the debt-summary widget shows the stored `current_principal` for that identical loan.
  Worked example: stored `LoanParams.current_principal = $300,000.00`; engine-walked
  `proj.current_balance = $297,450.00` after confirmed payments. `/savings` debt card
  `total_debt` includes `$300,000.00` (`:840,855`); the same page's account card shows
  `$297,450.00` (`:373`); net worth subtracts `$297,450.00` (`:2079-2081`). Three figures for
  one loan, no error raised.
- Verdict: **UNKNOWN** for the canonical aggregate-debt base -- blocked on **Q-15**
  (`09_open_questions.md:621-658`). The internal A-inconsistency (stored vs engine within one
  service) is a recorded DIVERGE independent of Q-15.
- If DIVERGE: classification: SOURCE_DRIFT (stored vs schedule/engine base), DEFINITION_DRIFT
  (A-04 base meaning differs across pages).
- Open questions for the developer: Q-15 (governing the canonical base). Cross-link Q-11
  (loan principal display) and the A-04 verification note (`09_open_questions.md:149-158`).

---

## Finding F-009: projected_end_balance (grid current period) vs checking_balance (/savings) -- symptom #1

- Concept: `projected_end_balance` / `checking_balance` (audit-plan section 3.1 mandatory #1)
- Symptom link: **#1** (`$160` grid vs `$114.29` `/savings`, both current pay period)
- Paths compared: A = grid current-period balance (`grid.py:243` ->
  `balance_calculator.calculate_balances`, `balances.get(current_period.id)`); B = `/savings`
  checking current balance (`savings_dashboard_service.py:343` -> `calculate_balances`,
  `current_bal = balances.get(current_period.id)` `:352`).
- Path A: `app/routes/grid.py:218-248` -- `period_ids = [p.id for p in all_periods]` `:218`; filters
  `pay_period_id in period_ids`, `scenario_id == scenario.id`, `is_deleted False`,
  `account_id == account.id` (`:219-225`); **`selectinload(Transaction.entries)` `:229`**; anchor
  `account.current_anchor_balance if account else 0.00` `:238`;
  `calculate_balances(... all_periods, all_transactions)` `:243`.
- Path B: `app/services/savings_dashboard_service.py:90-100,320-352` -- preload filters
  `pay_period_id in period_ids`, `scenario_id == scenario.id`, `is_deleted False` (`:95-97`);
  **NO `selectinload(entries)`** `:92-100`;
  `acct_transactions = [txn ... if txn.account_id == acct.id]` `:320-323`; anchor
  `acct.current_anchor_balance or 0.00` `:325`;
  `calculate_balances(... all_periods, acct_transactions)` `:343`;
  `current_bal = balances.get(current_period.id)` `:352`.
- Compared dimensions:
  - Status filter: identical -- both call `calculate_balances`, which gates
    `status_id != projected_id` in `_sum_remaining`/`_sum_all`
    (`balance_calculator.py:411-412,443-444`). Projected-only on both. AGREE.
  - Transaction-type filter: identical (engine `is_income`/`is_expense`; shadow txns included
    identically). AGREE.
  - Effective-amount logic: **THE divergence.** Path A loaded entries (`grid.py:229`) so
    `_entry_aware_amount` runs the entry formula
    `max(estimated - cleared_debit - sum_credit, uncleared_debit)` (`balance_calculator.py:383-386`)
    for Projected envelope expenses. Path B did NOT load entries
    (`savings_dashboard_service.py:92-100`) so `_entry_aware_amount` short-circuits at
    `'entries' not in txn.__dict__` (`balance_calculator.py:353-354`) and returns `effective_amount`
    = `estimated_amount` (Projected, `actual_amount` NULL). Same engine, same anchor, same periods,
    same scenario; the only input difference is the `selectinload`.
  - Period scope: both `all_periods`, both read `balances.get(current_period.id)`. AGREE.
  - Anchor handling: A `... if account else ...`; B `... or ...`. Identical result unless
    `current_anchor_period_id` is None (Q-16); not the symptom-#1 driver.
  - Scenario filter: both `get_baseline_scenario` (`grid.py:177`,
    `savings_dashboard_service.py:85`). AGREE.
  - is_deleted handling: both `Transaction.is_deleted.is_(False)` at query (`grid.py:222`,
    `savings_dashboard_service.py:97`). AGREE.
  - Quantization: none either path. AGREE.
  - Source-of-truth column read: both computed from the anchor + Projected txns; neither reads a
    stored balance. AGREE.
- Divergences:
  - Single pinned dimension: `selectinload(Transaction.entries)` present at `grid.py:229` and absent
    at `savings_dashboard_service.py:92-100`, driving `_entry_aware_amount`
    (`balance_calculator.py:353-354` vs `:383-386`) to two different per-expense values for the
    identical transaction. Every other dimension AGREES.
- Risk / worked reconstruction of `$160` vs `$114.29`: Let the checking anchor be `A` and the
  current period contain Projected envelope expenses whose entries are partly cleared. Grid
  (entries loaded) holds back `sum(max(est - cleared - credit, uncleared))`; `/savings` (entries
  not loaded) holds back `sum(estimated_amount)`. Grid balance =
  `A - other - sum(entry_aware_expense)`; `/savings` balance =
  `A - other - sum(estimated_expense)`. Since `entry_aware_expense <= estimated` whenever
  `cleared_debit + sum_credit > uncleared shortfall`, **grid >= /savings**, matching
  `$160 > $114.29`. The gap `$160 - $114.29 = $45.71` equals
  `sum(estimated_amount - max(estimated - cleared_debit - sum_credit, uncleared_debit))` over
  the period's Projected envelope expenses -- i.e. `$45.71` of debit/credit entry value already
  reflected in the checking anchor that `/savings` double-subtracts because it never loaded the
  entries to know they cleared. Numeric illustration that reproduces the exact symptom shape
  with one expense: anchor `A` chosen so grid current-period balance is `$160.00`; one Projected
  envelope expense `estimated_amount = $X`, cleared debit entries `= $45.71`, no credit/uncleared.
  Grid expense contribution `= max(X - 45.71 - 0, 0) = X - 45.71`; `/savings` contribution
  `= X`. Grid balance `= K - (X - 45.71) = 160.00` => `/savings` balance
  `= K - X = 160.00 - 45.71 = $114.29`. The exact `X` and entry rows are the developer's data
  (Phase 5 reconstructs the per-account ledger); the mechanism, sign, and the controlling
  dimension are pinned here with source citations.
- Verdict: DIVERGE
- If DIVERGE: classification: SILENT_DRIFT (an implementation-detail input -- whether the
  consuming query selectinloaded `entries` -- silently changes a user-facing balance with no
  error and no label, violating developer expectation E-04, `00_priors.md:178-182`).
- Open questions for the developer: none required to classify (E-04 makes the unlabeled
  cross-page difference a finding by definition). Cross-link Q-10 (the same `_entry_aware_amount`
  vs raw-`effective_amount` choice governs the grid subtotal, F-004) and Q-16 (anchor-None).

---

## Finding F-010: _sum_remaining vs _sum_all consistency

- Concept: `checking_balance` / `period_subtotal` internals (audit-plan section 3.1 mandatory #3)
- Symptom link: none (verifies the engine's two summation helpers do not hide a filter drift)
- Paths compared: A = `_sum_remaining` (`balance_calculator.py:389`, anchor period); B = `_sum_all`
  (`balance_calculator.py:422`, non-anchor periods). One pair.
- Path A: `app/services/balance_calculator.py:389-419` -- Read in full.
- Path B: `app/services/balance_calculator.py:422-451` -- Read in full.
- Compared dimensions (expanding both helper bodies inline):
  - Status filter: A `projected_id = ref_cache.status_id(StatusEnum.PROJECTED)` `:406`;
    `if txn.status_id != projected_id: continue` `:411-412`. B `projected_id = ...` `:439`;
    `if txn.status_id != projected_id: continue` `:443-444`. **Identical.**
  - Transaction-type filter: A `if txn.is_income: ... elif txn.is_expense: ...` `:414-417`. B
    `if txn.is_income: ... elif txn.is_expense: ...` `:446-449`. **Identical.**
  - Effective-amount logic: A income `+= txn.effective_amount` `:415`, expense
    `+= _entry_aware_amount(txn)` `:417`. B income `+= txn.effective_amount` `:447`, expense
    `+= _entry_aware_amount(txn)` `:449`. **Identical.**
  - Period scope: neither helper knows about periods -- both receive a pre-grouped `transactions`
    list; the anchor-vs-non-anchor distinction lives entirely in the **caller**
    `calculate_balances`: anchor period does `running_balance = anchor_balance + income - expenses`
    (`:74-75`), post-anchor does `running_balance = running_balance + income - expenses` (`:79-80`).
    The helpers themselves are period-agnostic. **The only intended difference (anchor seed vs
    roll-forward) is in the caller, not in these two helpers.**
  - Anchor / scenario / is_deleted / quantization / source column: not handled in either helper
    (pure summation of the passed list). Identical (both none).
- Divergences:
  - None in behavior. The two functions have **byte-identical bodies** (`:403-419` vs
    `:436-451`); only the docstrings differ (`:390-401` "anchor period" vs `:423-434`
    "non-anchor period"). No status, type, or effective-amount difference sneaks in. (This exact
    duplication is a DRY observation for Phase 6, not a Phase-3 consistency divergence.)
- Risk: None for consistency -- the helpers cannot disagree because they are the same code. The
  only behavioral difference between the anchor and non-anchor period is the caller's seed
  (`anchor_balance + ...` vs `previous + ...`), which is the intended and documented anchor
  semantics (`balance_calculator.py:8-15` docstring).
- Verdict: AGREE
- If DIVERGE: n/a.
- Open questions for the developer: none. (Phase 6 DRY note: `_sum_remaining` and `_sum_all`
  are identical and should likely collapse to one parameter-free helper; recorded for
  `06_dry_solid.md`, not actioned here -- hard rule: no code changes.)

---

## Finding F-011: credit-status handling across balance producers

- Concept: cross-cutting (`checking_balance`/`account_balance`/`period_subtotal`; audit-plan
  section 3.1 mandatory #4)
- Symptom link: none (verifies Credit is excluded everywhere the grid excludes it)
- Paths compared: grid balance (`_sum_remaining`/`_sum_all`), grid subtotal (`grid.py:263-279`),
  `/savings` & `/accounts` & dashboard (same engine), net-worth `_get_account_balance_map`, the
  loan variant `calculate_balances_with_amortization`, and the `effective_amount` property.
- Path evidence (each Read at source):
  - Grid balance / `/savings` / `/accounts` / dashboard: `_sum_remaining`/`_sum_all` gate
    `status_id != projected_id` (`balance_calculator.py:411-412,443-444`) -- Credit (id !=
    Projected) is excluded.
  - Grid subtotal: `if txn.is_deleted or txn.status_id != projected_id: continue` (`grid.py:269`) --
    Credit excluded.
  - `effective_amount` property: tier 2
    `if self.status and self.status.excludes_from_balance: return Decimal("0")`
    (`transaction.py:240-241`); Credit has `excludes_from_balance = True` (`ref_seeds.py:82`) ->
    contributes `0` even on any path that reads `effective_amount` directly.
  - Loan variant `calculate_balances_with_amortization`: payment-detection gate is
    `if txn.status and txn.status.excludes_from_balance: continue` (`balance_calculator.py:264`) --
    Credit (`excludes_from_balance = True`) excluded; this gate ALSO admits Paid/Received/Settled
    (they are `excludes_from_balance = False`), which is the intended loan-payment behavior, not a
    credit divergence.
  - net-worth `_get_account_balance_map`: routes through `calculate_balances`/`_with_interest`
    (`year_end_summary_service.py:2108,2127`) -> same Projected-only gate -> Credit excluded.
- Compared dimensions:
  - Status filter: two distinct mechanisms, both exclude Credit -- (i) `status_id != projected_id`
    (grid balance, grid subtotal, all `calculate_balances` consumers); (ii) `excludes_from_balance`
    (the `effective_amount` tier-2 short-circuit and the amortization variant). Credit is excluded
    by BOTH. AGREE on the outcome.
  - Transaction-type / scenario / is_deleted / period / quantization: not credit-specific; covered
    by F-001/F-002.
  - Source column: n/a.
- Divergences:
  - No Credit-inclusion divergence found among balance producers. The mechanism differs
    (`!= projected` vs `excludes_from_balance`) but for Credit the result is identical
    (Credit is neither Projected nor `excludes_from_balance = False`). The mechanism difference
    is only observable for Paid/Received/Settled in the loan variant, which is intended
    loan-payment detection, not a credit issue.
  - Tooling note: audit-plan 3.1 names "the chart data service" as a place that must also
    exclude Credit; grep-verified that `app/services/chart_data_service.py` was removed in commit
    `e3b3a5e` (only stale `.pyc` remains), so there is no live chart_data_service to check; the
    live chart-series producers route through `calculate_balances_with_interest` (Credit
    excluded) or per-domain engines that do not sum transaction status (P3-b/d).
- Risk: None for credit handling -- a Credit-status transaction contributes `Decimal("0")` to
  every balance/subtotal producer in the family, by one of the two mechanisms above.
- Verdict: AGREE
- If DIVERGE: n/a.
- Open questions for the developer: none. (Phase 6 note: the two parallel exclusion mechanisms
  -- `status_id != projected_id` inline vs `status.excludes_from_balance` -- are a centralization
  candidate; recorded for `06_dry_solid.md`.)

---

## Finding F-012: shadow-transaction handling (Transfer Invariant 5)

- Concept: cross-cutting balance integrity (audit-plan section 3.1 mandatory #5; CLAUDE.md
  Transfer Invariant 5; priors E-09, `00_priors.md:202-204`)
- Symptom link: none (verifies the balance calculator never double-counts transfers)
- Paths compared: the balance engine `app/services/balance_calculator.py` (whole module) vs the
  invariant "queries ONLY budget.transactions, NEVER budget.transfers".
- Evidence (grep re-run this session and Read at source):
  - `grep -n "Transfer\|transfers\|budget.transfers\|from app.models" app/services/balance_calculator.py`
    -> only matches are docstring prose at `:17` ("Transfer effects are included automatically via
    shadow transactions") and `:19` ("The calculator does NOT query or process Transfer objects
    directly"). No `from app.models.transfer` import, no `db.session.query(Transfer)`, no
    `budget.transfers` reference anywhere in the 451-line module.
  - The module's only imports are `interest_projection.calculate_interest` (`:27`), a function-local
    `from app.services.amortization_engine import ...` (`:202-204`), `ref_cache` (`:31`), and
    `StatusEnum` (`:32`). None touch the `Transfer` model.
  - Shadow transactions are consumed as ordinary `Transaction` rows: the engine groups all passed
    `transactions` by `pay_period_id` (`:62-64`) and sums them via `_sum_remaining`/`_sum_all`;
    transfer effects enter only through the shadow `Transaction` rows the caller already loaded
    (callers filter `Transaction`, e.g. `grid.py:226-234`), never through a `Transfer` query.
- Compared dimensions:
  - Source-of-truth read: balance engine reads only `Transaction` (the passed list); zero
    `Transfer`/`budget.transfers` reads. Matches the invariant exactly.
  - Status / type / scenario / is_deleted / period / quantization: covered by F-001/F-002; not the
    invariant axis.
- Divergences:
  - None in `balance_calculator.py`. Invariant 5 HOLDS for the balance calculator (the module the
    invariant names). (Catalog F4 / Gate F notes a separate year-end `_compute_transfers_summary`
    money aggregate over `budget.transfers` for a display total -- that is a `transfer_amount`
    concern owned by P3-d, not a balance-calculator violation; cross-link only.)
- Risk: None -- the double-counting risk the invariant guards against (a transfer counted once
  as a `Transfer` row and again as its two shadow `Transaction` rows) cannot occur in the
  balance calculator because it never queries `Transfer`.
- Verdict: AGREE
- If DIVERGE: n/a.
- Open questions for the developer: none. (P3-d owns the `budget.transfers` read inventory and
  the `_compute_transfers_summary` double-count cross-check per catalog Gate F4.)

---

## P3-a verification (a-e)

- **(a) E1 balance rows reconciled.** E1 balance-family rows = 8 (`account_balance`,
  `checking_balance`, `projected_end_balance`, `period_subtotal`, `chart_balance_series`,
  `net_worth`, `savings_total`, `debt_total`); findings produced = 8 (F-001..F-008), mapped 1:1
  in the table above. No E1 balance row skipped; no balance-family token in E1's single-path
  list (`cash_runway_days` P2-a-deferred to P3-b/c, stated). **E1 balance rows: 8; findings
  produced: 8. HOLDS.**
- **(b) Five mandatory section-3.1 comparisons each have a finding.** #1 grid-projected vs
  `/savings`-checking -> F-009; #2 account_balance grid vs `/accounts` vs `/savings` vs
  dashboard vs net-worth -> F-001 (3-way+); #3 `_sum_remaining` vs `_sum_all` -> F-010;
  #4 credit-status everywhere -> F-011; #5 shadow / Invariant 5 -> F-012. **5/5 present.
  HOLDS.**
- **(c) Every DIVERGE has a concrete worked example.** F-001 (`$962.34` vs `$500.00`),
  F-002 (`$45.71` gap mechanism), F-003 (loan stored vs engine), F-008 (`$300,000` vs
  `$297,450` vs `$300,000`), F-009 (full `$160`->`$114.29` reconstruction). UNKNOWN-verdict
  findings (F-004 Q-10, F-006/F-007/F-008 Q-15) still carry worked numeric divergences for the
  axes that hold independent of the blocking Q. **HOLDS.**
- **(d) Self spot-check -- 5 random divergence bullets re-Read at source:**
  1. F-002 "grid subtotal uses raw `effective_amount` `grid.py:274`": re-Read `grid.py:274`
     -> `expense += txn.effective_amount` (NOT `_entry_aware_amount`). **Confirmed.**
  2. F-009 "`/savings` preload has no `selectinload(entries)`
     `savings_dashboard_service.py:92-100`": re-Read `:92-100` ->
     `db.session.query(Transaction).filter(...).all()`, no `.options(...)`. **Confirmed.**
  3. F-008 "`principal = Decimal(str(lp.current_principal))` `savings_dashboard_service.py:840`":
     re-Read `:840` -> exactly that; `total_debt += principal` at `:855`. **Confirmed.**
  4. F-006 "net-worth `_get_account_balance_map` returns None on anchor-None `:2065-2066`":
     re-Read `:2065-2066` -> `if account.current_anchor_period_id is None: return None`.
     **Confirmed.**
  5. F-010 "`_sum_remaining`/`_sum_all` byte-identical bodies": re-Read `:403-419` and
     `:436-451` -> identical statements (`projected_id`; `if txn.status_id != projected_id:
     continue`; `if txn.is_income: income += txn.effective_amount`; `elif txn.is_expense:
     expenses += _entry_aware_amount(txn)`). **Confirmed.**
- **(e) Every UNKNOWN names the blocking Q-NN and the resolving answer.** F-004 -> Q-10
  (developer states whether subtotal is a display detail or a shared concept, and the canonical
  expense formula). F-006 -> Q-15 (which per-account dispatcher is canonical; W-152/W-159
  direction). F-007 -> Q-15 (canonical savings aggregator). F-008 -> Q-15 (canonical
  aggregate-debt base). F-001/F-003 carry an UNKNOWN sub-axis -> Q-15/Q-11. New question
  **Q-16** (anchor-None handling divergence) filed in `09_open_questions.md`. **HOLDS.**

P3-a complete. Phase 3 is NOT complete -- P3-b (loan/income), P3-c, P3-d, P3-watchlist, and
P3-reconcile remain. P3-reconcile is the Phase-3 completion gate. Not committed; developer reviews
between sessions.

---

## P3-b: loan / debt family (F-013..F-026)

Session P3-b, 2026-05-15. Scope: `monthly_payment`, `loan_principal_real`, `loan_principal_stored`,
`loan_principal_displayed`, `principal_paid_per_period`, `interest_paid_per_period`,
`escrow_per_period`, `total_interest`, `interest_saved`, `months_saved`, `payoff_date`,
`loan_remaining_months`, `dti_ratio`. Read-only (audit plan section 0). Every divergence bullet
below was Read at the cited `file:line` in THIS session, not inferred from the P2 catalog. Symptom
links: #2 (mortgage payment $1911.54/$1914.34/$1912.94 -> $1910.95) -> F-013; #3 (current_principal
not updating as transfers settle) -> F-014; #4 (5/5 ARM payment fluctuating inside the fixed-rate
window) -> F-026. A-04/A-05 are resolved developer answers (`09_open_questions.md`, verification
notes 2026-05-15: A-04 ACCURATE, A-05 PARTIAL -> 16 sites); Q-09/Q-11/Q-15 are PENDING -> concepts
gated on them get verdict UNKNOWN, never a guessed verdict.

### E1 loan/debt-row reconciliation (verification a)

E1 register (`02_concepts.md:3215-3271`) loan/debt rows = **13**: `monthly_payment`,
`loan_principal_real`, `loan_principal_stored`, `loan_principal_displayed`,
`principal_paid_per_period`, `interest_paid_per_period`, `escrow_per_period`, `total_interest`,
`interest_saved`, `months_saved`, `payoff_date`, `dti_ratio` (12 multi-path) +
`loan_remaining_months` (1 single-path scoped internal-verify, `02_concepts.md:3259-3261`). Findings
F-013..F-025 = **13**, mapped 1:1 below. F-026 is the standalone audit-plan section-3.1 mandatory #3
(5/5 ARM hand-computation, symptom #4) -- it has no single E1 row of its own, exactly as P3-a's
F-009 was the standalone symptom-#1 finding. The four section-3.1 mandatory loan/debt comparisons
map:
## 1 (real vs stored vs displayed; symptom #3) -> F-014; #2 (16-site monthly_payment pairwise;
symptom #2) -> F-013; #3 (5/5 ARM hand computation; symptom #4) -> F-026; #4 (total_interest /
interest_paid_per_period / escrow_per_period A-06 tie) -> F-018/F-019/F-020.

| E1 loan/debt row | Finding |
| --- | --- |
| `monthly_payment` | F-013 (also satisfies 3.1 #2 / symptom #2) |
| `loan_principal_real` | F-014 (also satisfies 3.1 #1 / symptom #3) |
| `loan_principal_stored` | F-015 |
| `loan_principal_displayed` | F-016 |
| `principal_paid_per_period` | F-017 |
| `interest_paid_per_period` | F-018 (3.1 #4 part) |
| `escrow_per_period` | F-019 (3.1 #4 part) |
| `total_interest` | F-020 (3.1 #4 part) |
| `interest_saved` | F-021 |
| `months_saved` | F-022 |
| `payoff_date` | F-023 |
| `loan_remaining_months` | F-024 (single-path internal verify) |
| `dti_ratio` | F-025 |
| (standalone 3.1 #3 / symptom #4) | F-026 |

**E1 loan/debt rows: 13; findings produced: 13** (F-013..F-025), plus F-026 standalone. Reconciled.

#### monthly_payment 16-site provenance substrate (Read at source this session; cited by F-013..F-026)

Per Phase 2 Gate D, `monthly_payment` is **16 call sites + 1 definition**, NOT the inventory's
14. `grep -n "calculate_monthly_payment(" app/ --include=*.py` re-run this session returns
exactly the 16 (plus the def at `amortization_engine.py:178`). Each `(principal, rate, n)` triple
Read at source:

| # | Site | Branch | principal | rate | n |
| --- | --- | --- | --- | --- | --- |
| def | `amortization_engine.py:178-197` | annuity; `quantize(TWO_PLACES, ROUND_HALF_UP)` `:192/197` | arg | arg | arg |
| 1 | `amortization_engine.py:436-438` | `generate_schedule`, `using_contractual` TRUE (`:430-434`) | `original_principal` | `annual_rate` | `term_months` |
| 2 | `:440-442` | `generate_schedule` ELSE | `current_principal` arg | `annual_rate` | `remaining_months` |
| 3 | `:491-493` | `generate_schedule` anchor reset (`anchor_balance` set, `pay_date>anchor_date`, `:486-487`) | `anchor_balance` | `current_annual_rate` | `months_left = max_months-month_num+1` `:490` |
| 4 | `:512-514` | `generate_schedule` rate change (`period_rate != current_annual_rate` `:502`) | running `balance` | `period_rate` from `_find_applicable_rate` `:499` | `months_left = max_months-month_num+1` `:511` |
| 5 | `:693-695` | `calculate_summary`, `original_principal is not None AND not has_rate_changes` `:692` | `original_principal` | `annual_rate` | `term_months` |
| 6 | `:697-699` | `calculate_summary` ELSE | `current_principal` arg | `annual_rate` | `remaining_months` |
| 7 | `:952-954` | `get_loan_projection`, `is_arm AND remaining>0` `:950` | `Decimal(str(params.current_principal))` `:913` STORED | `Decimal(str(params.interest_rate))` `:914` STORED | `calculate_remaining_months(origination_date, term_months)` `:908-910` |
| 8 | `:957-959` | `get_loan_projection` ELSE (fixed OR fully-paid ARM) | `Decimal(str(params.original_principal))` `:912` | `:914` STORED | `params.term_months` |
| 9 | `balance_calculator.py:225-229` | `calculate_balances_with_amortization`, `is_arm` `:220-221` | `loan_params.current_principal` STORED `:226` | `loan_params.interest_rate` STORED `:216` | `calculate_remaining_months` `:222-224` |
| 10 | `:231-235` | ELSE | `loan_params.original_principal` `:232` | `:216` STORED | `loan_params.term_months` |
| 11 | `loan_payment_service.py:251-255` | `compute_contractual_pi`, `params.is_arm` `:250` | `current_principal` STORED `:252` | `interest_rate` STORED `:253` | `calculate_remaining_months` `:247-249` |
| 12 | `:256-260` | ELSE | `original_principal` `:257` | `interest_rate` STORED `:258` | `params.term_months` |
| 13 | `routes/loan.py:1102-1104` | `refinance_calculate`, UNCONDITIONAL (new-loan terms by design) | `refi_principal` (`data["new_principal"]` OR `proj.current_balance + closing_costs` `:1092-1095`) | `pct_to_decimal(data["new_rate"])` form `:1098` | `data["new_term_months"]` form `:1099` |
| 14 | `routes/loan.py:1225-1229` | `create_payment_transfer`, `params.is_arm` `:1221` | `current_principal` STORED `:1226` | `interest_rate` STORED `:1227` | `calculate_remaining_months` `:1222-1224` |
| 15 | `:1231-1235` | ELSE | `original_principal` `:1232` | `interest_rate` STORED `:1233` | `params.term_months` |
| 16 | `routes/debt_strategy.py:127-129` | `_load_debt_accounts`, **UNCONDITIONAL ARM-style on EVERY loan, no `is_arm` branch on the formula** | `real_principal` (`_compute_real_principal` `:120-124`: ARM stored `current_principal` `:172-173`; fixed confirmed-replay last `is_confirmed.remaining_balance` `:193-195`, fallback stored `:197`) | `Decimal(str(params.interest_rate))` STORED `:110` | `calculate_remaining_months` `:111-113` |

Two source-read facts that the catalog did not surface (recorded here, P2 not modified --
append-only):

- **Sites 9 & 10 are DEAD.** In `calculate_balances_with_amortization` the `monthly_payment`
  computed at `:225-229`/`:231-235` is never referenced again. The per-period principal split
  uses `interest_portion = (running_principal * monthly_rate).quantize(...)`
  (`balance_calculator.py:274-276`) and `principal_portion = total_payment_in - interest_portion`
  (`:277`); `monthly_payment` does not appear after `:235`. The catalog's claim
  (`02_concepts.md:931-934`) that `principal_paid_per_period` "uses the ARM/fixed-discriminated
  monthly payment from sites 9/10" is a producer-attribution artifact; F-017 records the actual
  mechanism.
- **The 5/5-ARM structural columns are inert.** `LoanParams.arm_first_adjustment_months` /
  `arm_adjustment_interval_months` (`loan_params.py:60-61`) are form-bound
  (`loan.py:670`, `_PARAM_FIELDS`) and schema-validated (`validation.py:1450-1451,1471-1472`)
  but consumed by **zero** calculation sites (`grep` this session: only model / route
  `_PARAM_FIELDS` / schema references). The amortization engine has no representation of the
  fixed-rate window. This is the F-026 root cause.

`as_of` default for every `calculate_remaining_months` call above is `today`
(`amortization_engine.py:136-137`); `n` is therefore identical across sites for the same
loan-on-date (per P2-b `loan_remaining_months`, F-024). The divergence axes are **principal** and
**rate** (and, for sites 3/4, the `months_left` loop-index `n` which is NOT the calendar
`remaining`).

---

### Finding F-013: monthly_payment consistency (16-site) -- symptom #2

- Concept: `monthly_payment` (audit-plan section 3.1 mandatory #2)
- Symptom link: **#2** ($1911.54 / $1914.34 / $1912.94 across views; $1910.95 after editing
  current principal). Inherits into #4 (F-026).
- Paths compared: the 16 call sites above, pairwise. The materially distinct comparison classes
  (every pair within a class enumerated; cross-class pairs are the discriminator/`n` seams):
  - **Displayed-ARM set {7, 11, 14}** (all: STORED `current_principal` + STORED `interest_rate`
    - calendar `remaining`). Site 9 is in this input class but DEAD.
  - **Schedule-internal {3, 4}** (anchor/rate-change re-amortization; `n = months_left`, a
    loop index, NOT calendar `remaining`; site 4 rate = rate-history `period_rate`).
  - **Contractual-fixed {1, 5, 8, 10, 12, 15}** (`original_principal` + STORED rate +
    `term_months`).
  - **Re-amort {2, 6}** (`current_principal` arg + calendar `remaining`).
  - **Strategy {16}** (`real_principal` + STORED rate + calendar `remaining`; ARM-formula on
    every loan).
  - **Refi {13}** (form inputs; new-loan, by design).
- Path 7 (the displayed "Monthly P&I"): `app/routes/loan.py:429-432` ->
  `amortization_engine.get_loan_projection` -> `:950-954` Read at source:
  `if is_arm and remaining > 0: monthly_payment = calculate_monthly_payment(current_principal,
  rate, remaining)` with `current_principal = Decimal(str(params.current_principal))` `:913`
  (STORED), `rate = Decimal(str(params.interest_rate))` `:914` (STORED),
  `remaining = calculate_remaining_months(params.origination_date, params.term_months)` `:908-910`.
  Rendered `loan/dashboard.html:129` `summary.monthly_payment`.
- Path 3 (the schedule per-row payment for the SAME ARM): `amortization_engine.py:486-493` Read
  at source -- at the first month with `pay_date > anchor_date` (anchor_date = `date.today()`
  for ARM, `:927`), `balance = anchor_balance` (= `current_principal`, `:926`),
  `months_left = max_months - month_num + 1` `:490`,
  `monthly_payment = calculate_monthly_payment(balance, current_annual_rate, months_left)`
  `:491-493`. `max_months = remaining_months` (`:455`, since `using_contractual` is False for
  ARM: `original=None` `:920` -> `:430-434` False). Rendered per-row at
  `loan/_schedule.html:54-55`.
- Path 4 (rate-history re-amortization): `amortization_engine.py:498-514` --
  `period_rate = _find_applicable_rate(pay_date, rate_schedule, annual_rate)` `:499-501`;
  if `period_rate != current_annual_rate`, `monthly_payment =
  calculate_monthly_payment(balance, current_annual_rate, months_left)` `:512-514` using the
  RateHistory-derived rate, NOT STORED `interest_rate`.
- Path 16 (debt-strategy "minimum payment"): `app/routes/debt_strategy.py:127-129` --
  `calculate_monthly_payment(real_principal, rate, remaining)`, `real_principal` from
  `_compute_real_principal` `:120-124` (fixed: confirmed-payment-reduced; ARM: stored).
- Compared dimensions:
  - Status / type / scenario / is_deleted: `calculate_monthly_payment` is a pure function of
    `(principal, rate, n)`; these axes do not enter it. The shadow-income query that feeds
    `payments` (`get_payment_history`, `loan_payment_service.py:202-214`) filters
    `is_deleted False`, `Status.excludes_from_balance False`, baseline scenario -- identical
    across the engine-routed paths; not the divergence axis for the scalar payment.
  - Effective-amount logic: `payments` amounts use `txn.effective_amount`
    (`loan_payment_service.py:218`); affects the SCHEDULE (sites 3/4 balance trajectory) but
    NOT the site-7 scalar (which reads STORED `current_principal`, not the replayed balance).
  - **Principal source: DIVERGES.** Site 7/11/14 = STORED `current_principal`;
    sites 1/5/8/10/12/15 = `original_principal`; sites 2/6 = `current_principal` arg passed by
    caller; site 13 = `proj.current_balance + closing_costs` or form; site 16 = `real_principal`
    (confirmed-payment-reduced for fixed). Cited above.
  - **Rate source: DIVERGES.** Sites 7-16 = STORED `LoanParams.interest_rate`
    (`amortization_engine.py:914`, etc.); site 4 = RateHistory `period_rate`
    (`_find_applicable_rate`, `:499`). For an ARM whose stored `interest_rate` differs from the
    current RateHistory entry, the displayed P&I (site 7, stored rate) and the schedule rows
    (site 4, history rate) use different rates.
  - **`n` source: DIVERGES.** Site 7 `n = calculate_remaining_months` (calendar:
    `max(0, term_months - months_elapsed)`, `amortization_engine.py:138-142`). Sites 3/4
    `n = max_months - month_num + 1` (loop index, `:490/:511`). For the same ARM-on-date these
    are not equal (`month_num` at the post-today anchor row ~= `months_elapsed`, so site-3
    `n ~= remaining + 1`; the off-by-one shifts the annuity payment by a few dollars).
  - **ORM load-context (mandatory dimension): DIVERGES for the schedule, not the site-7
    scalar.** `get_loan_projection`'s `payments`/`rate_changes` are whatever the caller passes.
    `loan.dashboard` (`:421` `_load_loan_context` -> `loan_payment_service.load_loan_context`
    -> `get_payment_history` query) passes A-06-prepared payments + RateHistory rate_changes;
    `_compute_total_payment` (`loan.py:399`) calls `get_loan_projection(params)` with
    `payments=None, rate_changes=None`. Same loan, two callers: the schedule (and therefore the
    per-row payment sites 3/4 and `proj.current_balance` for fixed) changes with the caller's
    load strategy even though the site-7 ARM scalar does not. SILENT.
  - Quantization: every site quantizes `TWO_PLACES, ROUND_HALF_UP` at the definition
    (`:192/197`) -- A-01-clean at the producer (A-01 verification, `09_open_questions.md:37-62`,
    confirms the engine is clean). Not the divergence axis.
  - Source-of-truth column: no stored/cached `monthly_payment` column exists (`grep` this
    session: zero `monthly_payment` columns on any model; `LoanParams` has none). Every value
    is recomputed per request from `(principal, rate, n)`.
- Divergences (each Read at source this session):
  - Site 7 vs site 3 for the same ARM-on-date: different `n`
    (`amortization_engine.py:908-910` vs `:490`) -> different annuity payment. The displayed
    "Monthly P&I" (`loan/dashboard.html:129`) and the schedule's post-anchor per-row payment
    (`loan/_schedule.html:55`) disagree. SILENT.
  - Site 7 vs site 4: different rate (STORED `:914` vs RateHistory `:499`) AND different `n`.
    SILENT.
  - Site 8 vs site 16 for a partially-paid FIXED loan: site 8 =
    `calculate_monthly_payment(original_principal, rate, term_months)` (contractual, the
    displayed Monthly P&I for fixed); site 16 =
    `calculate_monthly_payment(real_principal_reduced, rate, remaining)` -> strictly LOWER.
    Loan dashboard and debt-strategy page show different payments for the same fixed loan.
    DEFINITION (A-09 flags site-16 intent unclear; Q-09).
  - Discriminator-type seam: sites 1-2 / 5-6 branch on caller-supplied
    `using_contractual = original_principal is not None and term_months is not None and not
    has_rate_changes` (`amortization_engine.py:430-434`); sites 7-16 branch on the `is_arm`
    column. A fixed-rate caller that omits `original_principal` routes through the
    `current_principal` re-amort branch (site 2) -> a different payment than the contractual
    sites. SILENT.
  - Site 9/10 DEAD (`balance_calculator.py:225-235` value never used) -- not a value divergence
    but a producer-attribution correction (see F-017).
- Risk / worked reconstruction of $1911.54 / $1914.34 / $1912.94 -> $1910.95: the same ARM
  mortgage yields different displayed payments because each surface assembles a different
  `(principal, rate, n)` triple, and site 7 re-amortizes a FROZEN principal over a SHRINKING
  term every month (the F-026 mechanism). Concretely, let the loan be ARM, STORED
  `current_principal = P`, STORED `interest_rate = r` (monthly `r/12`), `term_months = T`, and
  `months_elapsed = e` at the moment of viewing:
  - Loan dashboard "Monthly P&I" = site 7 = `pmt(P, r/12, T - e)`.
  - Amortization schedule per-row (same page) = site 3 = `pmt(P, r/12, (T) - month_num + 1)`
    where `month_num` at the first post-today row ~= `e + 1` (origination + 1 to first month
    after today) -> `pmt(P, r/12, T - e)` evaluated at a DIFFERENT integer `n` than site 7 (off
    by ~1). For `P = $400,000`, `r = 0.065`, `T = 360`, `e = 48`: site 7 `n = 312`,
    site 3 `n ~= 311`; `pmt(400000, 0.065/12, 312) = $2,628.xx` vs
    `pmt(400000, 0.065/12, 311) = $2,631.xx` -- a few-dollar gap of exactly the symptom's shape.
  - Debt-strategy "minimum payment" = site 16; for an ARM `real_principal = P` (stored,
    `_compute_real_principal:172-173`) so site 16 ~= site 7 (same triple) -- but if the user's
    symptom loan is FIXED, site 16 uses the confirmed-payment-reduced principal -> a third,
    lower number.
  - "$1910.95 after editing current principal on /accounts/3/loan": `update_params`
    (`loan.py:672-674`, `setattr(params, "current_principal", value)`) writes the STORED column;
    site 7 immediately recomputes `pmt(P_new, r/12, T - e)` with the smaller `P_new` ->
    a lower payment. The drop direction matches (lower principal -> lower annuity payment).
  The exact loan parameters (origination, rate, term, stored principal) cannot be pinned without the
  developer's `/accounts/3/loan` data; the
  **mechanism, the controlling site citations, and the sign are pinned** (Phase 5 reconstructs the
  per-loan ledger). The fluctuation is an **inconsistent-inputs** problem (A-05 verification), not a
  formula conflict -- the single annuity formula at `:178-197` is correct; the 16 sites feed it
  incompatible triples.
- Verdict: **DIVERGE** for the cross-site axes that hold independently of any developer answer
  (principal source, rate source, `n` source, ORM load-context, discriminator seam). The
  site-16 `debt_strategy.py:127` *intent* (ARM-formula on every loan) is **UNKNOWN, blocked on
  Q-09**.
- If DIVERGE: classification: SILENT_DRIFT (site-7-vs-3/4 `n`/rate; discriminator seam; ORM
  load-context), DEFINITION_DRIFT (contractual vs real-principal payment, site 8 vs 16),
  PLAN_DRIFT (W-048 ARM-method `planned-per-plan`, `00_priors.md:295`; A-05 says any
  in-fixed-window divergence is a finding).
- Open questions for the developer: **Q-09** (`09_open_questions.md:325-371`; `debt_strategy.py:127`
  unconditional ARM-formula intent + the 16-site count -- A-09 proposed, pending). Cross-link
  E-02 (`00_priors.md:166-170`), A-05 verification (`09_open_questions.md:182-214`), C-04
  (`00_priors.md` C-04/A-05). New question raised: **Q-17** (site-7 re-amortization-of-frozen-
  principal; filed in `09_open_questions.md`, governs F-013/F-026).

---

### Finding F-014: loan_principal_real consistency -- symptom #3 (current_principal update path)

- Concept: `loan_principal_real` (audit-plan section 3.1 mandatory #1)
- Symptom link: **#3** (current principal does not update as transfers to the mortgage settle);
  contributes to #5. Cross-references **F-008** (debt_total stored-vs-engine internal
  inconsistency).
- Paths compared: A = `get_loan_projection.current_balance`
  (`amortization_engine.py:977-984`); B = stored `LoanParams.current_principal`
  (`loan_params.py:54`) as rendered on `/accounts/<id>/loan`
  (`loan/dashboard.html:104`); C = `_compute_real_principal`
  (`debt_strategy.py:147-197`, independent confirmed-payment replay). Pairs A-B, A-C, B-C.
- Path A: `app/services/amortization_engine.py:977-984` Read at source -- `if is_arm:
  cur_balance = current_principal` (`:978`, STORED, the A-04 ARM policy); `else: cur_balance =
  current_principal` then `for row in reversed(schedule): if row.is_confirmed: cur_balance =
  row.remaining_balance; break` (`:980-984`, fixed-rate engine-walk; fallback STORED when no
  confirmed rows).
- Path B: `app/routes/loan.py:553-557` passes `params=params` to the template; the schedule's engine
  value `proj` is computed (`:429`) but NOT passed for the card. `loan/dashboard.html:104` Read at
  source: `${{ "{:,.2f}".format(params.current_principal|float) }}`.
- Path C: `app/routes/debt_strategy.py:147-197` Read at source -- ARM: `return principal`
  (stored, `:172-173`); fixed: `payments = get_payment_history(...)` `:175` (RAW, no A-06
  preprocessing), `generate_schedule(... payments=payments)` `:181-190`, `for row in
  reversed(schedule): if row.is_confirmed: return row.remaining_balance` `:193-195`, fallback
  `return principal` `:197`.
- The symptom-#3 update-path determination (mandatory per audit plan section 3.1 / E-03,
  `00_priors.md:172-176`): **No code path recomputes or writes the STORED
  `LoanParams.current_principal` when a transfer to the loan account settles.** Proven this
  session by `grep -rn "current_principal\s*=" app/ --include=*.py`: the only assignments are
  the model column (`loan_params.py:54`), two Marshmallow schema field declarations
  (`validation.py:1444,1466`), and constructor kwargs for engine inputs
  (`current_principal=orig_principal`/`=real_principal`/`=params...`). The sole *writer* of the
  column is `update_params` (`loan.py:672-674`): `for field, value in data.items(): if field in
  _PARAM_FIELDS: setattr(params, field, value)` with `"current_principal"` in `_PARAM_FIELDS`
  (`:669`) -- a manual form-bind behind `_update_schema.load(request.form)` (`:653`). The
  transfer-settle path (`transaction_service.settle_from_entries` / transfer mark-done) writes
  **no** principal side-effect (no `current_principal` write anywhere in the settle code).
- Compared dimensions:
  - Source-of-truth column read: **DIVERGES.** A-ARM and B read STORED `current_principal`;
    A-fixed walks the engine schedule's last `is_confirmed` row; C-fixed independently replays
    confirmed payments (a second implementation of the same A-04 fixed-rate policy).
  - **ORM load-context (mandatory): DIVERGES for A-fixed.** A-fixed `cur_balance` =
    `row.remaining_balance` of the last `is_confirmed` row only if the caller passed `payments`
    containing `is_confirmed=True` records. A caller passing `payments=None`
    (`_compute_total_payment`, `loan.py:399`; `get_loan_projection(params)` with no payments)
    yields zero confirmed rows -> A-fixed falls back to STORED `current_principal`
    (`amortization_engine.py:980`). The same fixed loan's "real principal" is the engine-walked
    value through `loan.dashboard` (`:429-431`, payments passed via `_load_loan_context`) but
    the STORED value through any no-payments caller. SILENT.
  - Status / scenario / is_deleted: `get_payment_history` (`loan_payment_service.py:202-214`)
    and `debt_strategy.get_payment_history` (same function) filter `is_deleted False`,
    `Status.excludes_from_balance False`, the passed `scenario_id`; `is_confirmed =
    status.is_settled` (`:227`). Identical filter on both replay paths A-fixed and C-fixed; the
    divergence is the replay being implemented twice, not a filter mismatch.
  - A-06 preprocessing: A-fixed (via `loan.dashboard` / `_compute_account_projections`) replays
    A-06-PREPARED payments (`load_loan_context` -> `prepare_payments_for_engine`); C-fixed
    replays RAW `get_payment_history` (`debt_strategy.py:175`, no escrow subtraction / biweekly
    redistribution). For an escrow-inclusive biweekly mortgage the two confirmed-payment-walked
    balances differ (C over-counts paydown -- escrow treated as principal). SCOPE (A-06).
  - Quantization: balances flow from `Numeric(12,2)` and engine per-row
    `quantize(TWO_PLACES, ROUND_HALF_UP)` (`amortization_engine.py:551,586,605`). Not the axis.
- Divergences:
  - Symptom #3 core: settling a transfer into the loan does not change STORED
    `current_principal` (no writer; proven by grep above). For an ARM, A = STORED
    (`amortization_engine.py:978`) and B = STORED (`loan/dashboard.html:104`) -> the displayed
    "Current Principal" card NEVER moves as transfers settle; only `update_params` moves it.
    For a fixed loan, A-fixed (engine-walk) DOES move with confirmed payments
    (`:981-984`) but B still renders STORED `params.current_principal`
    (`loan/dashboard.html:104`, not `proj.current_balance`) -> the card still does not move.
    SOURCE.
  - A-fixed vs C-fixed: two independent confirmed-payment replays
    (`amortization_engine.py:980-984` vs `debt_strategy.py:181-195`), one A-06-prepared, one
    raw -> different real principal for the same fixed loan-on-date. SOURCE/SCOPE.
- Risk -- worked example (symptom #3): ARM mortgage, STORED `current_principal = $312,000.00`,
  `is_arm = True`. The user transfers the monthly payment; the shadow income on the loan
  account settles (status -> Settled, `is_settled = True`). `get_payment_history` now returns a
  confirmed PaymentRecord, but `get_loan_projection` for an ARM returns
  `cur_balance = current_principal = $312,000.00` (`amortization_engine.py:978`) -- the
  schedule's confirmed rows are NOT consulted for ARM. `loan/dashboard.html:104` renders
  `$312,000.00` unchanged. Three more confirmed payments settle: card still `$312,000.00`. The
  user then edits the field to `$309,500.00` via `update_params`; now the card and site-7
  Monthly P&I jump. Contrast a FIXED $312,000 loan: after the same confirmed payments,
  `get_loan_projection.current_balance` (`:981-984`) walks to e.g. `$310,847.12`, the refinance
  page (F-016 page 4,`proj.current_balance`) shows`$310,847.12`, but the dashboard card
  (`loan/dashboard.html:104`, STORED) still shows `$312,000.00`. E-03 ("the real loan principal
  reflects the principal portion") holds internally only for fixed-rate via the engine walk and
  is NOT surfaced on the primary card; for ARM the stored column is authoritative (A-04) and is
  simply never maintained, so E-03 is unmet for ARM until manual edit.
- Verdict: **DIVERGE.** A-04 makes the ARM-stored / fixed-walked split INTENDED (so the
  ARM-vs-fixed handling difference is not itself the finding); the finding is (1) the stored
  column has no settle-driven update path (symptom #3, provable from code), (2) the displayed
  card renders STORED regardless of loan type (B), and (3) A-fixed vs C-fixed are two replays
  that can disagree (A-06). The "which is canonical for the aggregate" axis is **UNKNOWN,
  blocked on Q-15**.
- If DIVERGE: classification: SOURCE_DRIFT (stored vs engine-walked; dual replay),
  SCOPE_DRIFT (A-06 raw-vs-prepared replay), SILENT_DRIFT (ORM load-context for A-fixed).
- Open questions for the developer: **Q-15** (`09_open_questions.md:621-658`; canonical
  per-account dispatcher / real-principal base). Cross-link **Q-11**
  (`09_open_questions.md:423-457`; which principal the card MUST show -> F-016), A-04
  verification (`09_open_questions.md:149-158`), E-03 (`00_priors.md:172-176`), **F-008**
  (the same stored-vs-engine split inside `_compute_debt_summary`).

---

### Finding F-015: loan_principal_stored consistency

- Concept: `loan_principal_stored`
- Symptom link: #3 / #5 (the stored column is the value the card shows and the debt card sums)
- Paths compared: A = stored `LoanParams.current_principal` (`loan_params.py:54`) as the ARM
  anchor consumed by `get_loan_projection` (`amortization_engine.py:926,978`); B = the same
  column rendered at `loan/dashboard.html:104`; C = `get_loan_projection.current_balance`
  fixed-rate engine-walk (`amortization_engine.py:980-984`). One pair of interest: B vs C
  (stored rendered vs engine-walked) for a partially-paid FIXED loan.
- Path A/B: Read at source -- `loan_params.py:54` `current_principal = db.Column(db.Numeric(12,
  2), nullable=False)`, CHECK `current_principal >= 0` (`:31-34`); `:926`
  `anchor_bal = current_principal if is_arm else None`; `:978` `cur_balance =
  current_principal` (ARM). `loan/dashboard.html:104` renders `params.current_principal|float`.
- Path C: `amortization_engine.py:980-984` (fixed-rate last-`is_confirmed`-row walk).
- Compared dimensions:
  - Source-of-truth column: B reads the stored column directly; C computes from the schedule.
    A-04: stored is **AUTHORITATIVE for ARM**, **CACHED-for-display for fixed**. So B==C for
    ARM by construction (`:978`); B != C for a fixed loan with confirmed payments.
  - Status / scenario / is_deleted / type / quantization: not applicable to a stored column
    read (B); C inherits the engine schedule filters (F-014). Not the axis.
  - ORM load-context: B is a pure column read (no query options affect it). C depends on
    `payments` being passed (F-014 ORM bullet). The stored column itself is load-context-free;
    the divergence is stored-vs-computed, not load-context (cross-link F-014).
  - Update path: the column's only writer is `update_params` (`loan.py:672-674`), proven in
    F-014. There is no recompute-on-settle; for a fixed loan the column is therefore a
    **stale-able mirror** (A-04 "CACHED-for-display").
- Divergences:
  - B (`loan/dashboard.html:104`, stored) vs C (`amortization_engine.py:980-984`,
    engine-walked) for a partially-paid fixed loan: the card shows the static stored value
    while the engine-real value (consumed by the refinance prefill, F-016 page 4) reflects
    confirmed payments. SOURCE.
- Risk -- worked example: fixed loan, STORED `current_principal = $250,000.00`, three confirmed
  payments since the user last edited the field. `get_loan_projection.current_balance`
  (`:980-984`) = `$248,910.44` (last confirmed row). `loan/dashboard.html:104` renders
  `$250,000.00`. The refinance page auto-prefill (`loan.py:1095`,
  `proj.current_balance + closing_costs`) uses `$248,910.44`. Same loan-on-date, two displayed
  principals; the stored card is the staler of the two.
- Verdict: **DIVERGE** (B-vs-C SOURCE divergence holds for fixed-with-confirmed-payments,
  independent of any pending Q; ARM coincides by A-04). The AUTHORITATIVE/CACHED *classification*
  is **Phase 4's** to assign (`04_source_of_truth.md`); Phase 3 records the divergence.
- If DIVERGE: classification: SOURCE_DRIFT (stored mirror vs engine-walked, fixed-rate).
- Open questions for the developer: cross-link **Q-11** (which principal the page MUST show ->
  F-016), A-04 (`09_open_questions.md:128-158`). No new question (Phase 4 owns the
  AUTHORITATIVE/CACHED verdict; the stored-vs-engine divergence itself is unambiguous given
  A-04). Cross-link **F-014**, **F-008**.

---

### Finding F-016: loan_principal_displayed consistency

- Concept: `loan_principal_displayed` (P2-b-resolved Appendix-A orphan; symptom #3/#5
  load-bearing)
- Symptom link: #3 (the card the user reads) / #5 (`/accounts/<id>/loan` vs other pages)
- Paths compared (the principal a user actually reads, same loan-on-date):
  - Page 1 = `loan/dashboard.html:104` "Current Principal" (bold accent card).
  - Page 4 = `loan/_refinance_results.html:69-70` current-real-principal-before-refi.
  - Page 5/6 = `debt_strategy/dashboard.html` + `_results.html` per-account principal.
  - Pairs: P1-P4, P1-P5/6, P4-P5/6.
- Path P1: `app/routes/loan.py:553-557` renders `params=params`; `loan/dashboard.html:104`
  Read at source = `${{ "{:,.2f}".format(params.current_principal|float) }}` -> **STORED**
  `loan_principal_stored`. `proj` is computed at `loan.py:429` but is NOT wired to the card.
- Path P4: `app/routes/loan.py:1087` `current_real_principal = proj.current_balance` (A-04
  dual: ARM stored, fixed engine-walked); `:1152` `"current_principal": current_real_principal`;
  `loan/_refinance_results.html:69-70` renders it -> **engine-real** `loan_principal_real`.
- Path P5/6: `app/routes/debt_strategy.py:139` `DebtAccount(current_principal=real_principal)`
  from `_compute_real_principal` `:147-197` -> **engine-real** (fixed: confirmed-replay; ARM:
  stored).
- Compared dimensions:
  - Source-of-truth column: P1 STORED column; P4 `proj.current_balance`; P5/6
    `_compute_real_principal`. Three bases for one loan's displayed principal.
  - ORM load-context: P4's `proj.current_balance` for a fixed loan depends on `payments` being
    passed (it is, via `_load_loan_context`, `loan.py:1063`); P5/6's `_compute_real_principal`
    fixed-branch issues its own `get_payment_history` (`debt_strategy.py:175`, RAW, no A-06).
    P1 is a pure stored read (load-context-free). So P4 (A-06-prepared replay) and P5/6 (raw
    replay) can disagree even though both are "engine-real". SILENT/SCOPE.
  - Status / scenario / is_deleted: P4/P5-6 replay paths use the same `get_payment_history`
    filter; P1 is a column read. Not the divergence axis.
  - Quantization: all render `"{:,.2f}".format(...|float)`; the `|float` on a `Decimal` is a
    display-only coding-standards note (cross-link E-16), not a value-divergence axis.
- Divergences:
  - P1 (STORED, `loan/dashboard.html:104`) vs P4 (`proj.current_balance`, `loan.py:1087`):
    for a partially-paid FIXED loan these differ (P1 stale stored, P4 engine-walked) -- the
    refinance prefill will not match the prominent dashboard card for the same loan-on-date.
    SOURCE.
  - P4 (A-06-prepared replay via `get_loan_projection`) vs P5/6 (`_compute_real_principal`
    RAW replay, `debt_strategy.py:175,181`): two engine-real values that diverge for an
    escrow-inclusive biweekly fixed mortgage. SOURCE/SCOPE.
  - P1 vs P5/6: stored vs raw-replay engine-real; same direction as P1-P4. SOURCE.
- Risk -- worked example: fixed mortgage, STORED `current_principal = $300,000.00`, confirmed
  payments have walked the engine balance to `$297,450.00`, escrow-inclusive biweekly so the
  RAW replay over-counts paydown to `$297,180.00`. Page 1 (`/accounts/<id>/loan` card) shows
  `$300,000.00`; Page 4 (refinance "current principal", auto-prefill base) shows `$297,450.00`;
  Page 5/6 (debt strategy starting balance) shows `$297,180.00`. Three numbers for one loan,
  no error raised, the most prominent (the bold card) is the stalest.
- Verdict: **UNKNOWN** -- `loan_principal_displayed` is PRIMARY PATH UNKNOWN: the codebase does
  not designate which principal a user-facing page MUST show. Blocked on **Q-11**
  (`09_open_questions.md:423-457`). The P1-vs-P4 and P4-vs-P5/6 divergences are recorded as
  facts regardless of Q-11's resolution (E-04, `00_priors.md:178-182`, makes an unlabeled
  cross-page difference a finding by definition); only the *verdict label* is gated because
  "which page is right" is the developer's call (audit plan section 9).
- If DIVERGE (conditional on Q-11): SOURCE_DRIFT (stored vs engine-real), SCOPE_DRIFT (A-06
  raw-vs-prepared replay between P4 and P5/6).
- Open questions for the developer: **Q-11** (governing; A-11 proposed, pending). Cross-link
  A-04, Q-15, **F-014**, **F-015**, **F-008**.

---

### Finding F-017: principal_paid_per_period consistency

- Concept: `principal_paid_per_period`
- Symptom link: #3 (per-period principal reduction is the mechanism E-03 describes)
- Paths compared: A = `generate_schedule` per-row `principal`
  (`amortization_engine.py:602`); B = `calculate_balances_with_amortization.principal_by_period`
  (`balance_calculator.py:283`); C = `_compute_debt_progress` jan1->dec31 delta
  (`year_end_summary_service.py:871`). Pairs A-B, A-C, B-C.
- Path A: `amortization_engine.py:520-602` Read at source -- when a PaymentRecord matches the
  month: `principal_portion = total_payment - interest` (`:531`), `interest = (balance *
  monthly_rate).quantize(TWO_PLACES, ROUND_HALF_UP)` (`:517`); otherwise `principal_portion =
  monthly_payment - interest` (`:566`). `balance` walked from `original_principal` at
  origination (or from `anchor_balance` post-anchor for ARM). Row `principal` quantized
  ROUND_HALF_UP (`:602`).
- Path B: `balance_calculator.py:260-287` Read at source -- per period:
  `total_payment_in = sum(txn.effective_amount for shadow-income txns)` (`:262-270`, gated
  `if txn.status and txn.status.excludes_from_balance: continue` `:264`, `txn.transfer_id is
  not None and txn.is_income` `:268-269`); `interest_portion = (running_principal *
  monthly_rate).quantize(Decimal("0.01"), ROUND_HALF_UP)` (`:274-276`);
  `principal_portion = total_payment_in - interest_portion` (`:277`), clamped
  `max(.,0)`/`min(.,running_principal)` (`:278-279`); `running_principal` seeded from
  `anchor_balance` (`:253`). **`monthly_payment` computed at `:225/231` (sites 9/10) is DEAD --
  never referenced after `:235`** (Read at source this session; corrects the catalog's stated
  mechanism `02_concepts.md:931-934`).
- Path C: `year_end_summary_service.py:865-871` Read at source -- `jan1_bal`/`dec31_bal` via
  `_balance_from_schedule_at_date` (`:1490-1521`, last row with `payment_date <= target`),
  `principal_paid = jan1_bal - dec31_bal` (`:871`), over schedules from
  `_generate_debt_schedules` (`:1453` `load_loan_context` -> A-06-prepared payments + ARM
  anchor `:1465-1483`).
- Compared dimensions:
  - Effective-amount logic: A uses the engine `total_payment` (a `PaymentRecord.amount` =
    `txn.effective_amount` at construction, `loan_payment_service.py:218`) minus
    interest-on-engine-balance; B uses `txn.effective_amount` summed live
    (`balance_calculator.py:270`) minus interest-on-`running_principal`; C is a balance delta
    of the engine schedule (no per-txn effective_amount). A's interest base = engine balance
    walked from origination/anchor; B's interest base = `running_principal` seeded from the
    account anchor. **Different interest bases -> different principal split** for the same
    period and same payment.
  - **A-06: DIVERGES.** A (via `get_loan_projection`/`_generate_debt_schedules`) and C consume
    A-06-PREPARED payments (escrow subtracted `loan_payment_service.py:305-319`, biweekly
    redistributed `:321-353`). B sums RAW shadow-income `effective_amount`
    (`balance_calculator.py:270`) with NO escrow subtraction -- for an escrow-inclusive
    payment, B attributes (payment - interest) including the escrow portion to principal,
    over-stating per-period principal paydown. SCOPE/DEFINITION (A-06).
  - **ORM load-context (mandatory): DIVERGES for B.** `total_payment_in`
    (`balance_calculator.py:270`) is summed over the caller's loaded transaction list; the
    `:264` `txn.status.excludes_from_balance` gate requires the `status` relationship -- if the
    caller did not eager-load it, it lazy-loads (correct value, N+1) but the *set* of
    transactions is the caller's query result (scenario / is_deleted / period filters). A and C
    consume the engine schedule, independent of the consuming route's transaction query. B's
    per-period principal therefore changes with the caller's transaction-load strategy. SILENT.
  - Status filter: A/C operate on PaymentRecords from `get_payment_history`
    (`Status.excludes_from_balance False`, `is_deleted False`); B excludes
    `status.excludes_from_balance` inline (`:264`) but INCLUDES Projected shadow income (no
    `status_id != projected` gate -- it is a debt-paydown projection, by design). Consistent in
    intent; the divergence is the interest base + A-06, not the status set.
  - Quantization: A `:602` and B `:274-276` both ROUND_HALF_UP; C is a subtraction of
    pre-quantized schedule balances. A-01-clean; not the axis.
- Divergences:
  - A vs B: interest base differs (engine balance from origination/anchor
    `amortization_engine.py:517` vs `running_principal` from account anchor
    `balance_calculator.py:274`); B additionally omits A-06 escrow subtraction. SILENT/SCOPE.
  - A vs C: same per-row formula, C is the year delta of the A-06-prepared schedule -- must
    equal `sum` of A's per-row `principal` for the year (W-295). Holds only if both read the
    same A-06-prepared schedule; `_generate_debt_schedules` (`:1453`) and
    `get_loan_projection` both use `load_loan_context`, so A and C are consistent BY
    CONSTRUCTION for the loan dashboard vs year-end. AGREE on the A-C pair (record explicitly).
  - B vs C: B raw-shadow-income split vs C A-06-prepared schedule delta -> diverge for
    escrow-inclusive biweekly mortgages. SCOPE.
- Risk -- worked example: ARM mortgage, monthly transfer `$2,400.00` (P&I `$1,900.00` + escrow
  `$500.00`), `running_principal`/anchor `$300,000.00`, `monthly_rate = 0.0054166`. Path B:
  `interest_portion = 300000 * 0.0054166 = $1,625.00`; `principal_portion = 2400.00 - 1625.00 =
  $775.00` (escrow `$500` wrongly counted toward principal). Path A/C (A-06-prepared): the
  payment is escrow-subtracted to `$1,900.00`; `principal_portion = 1900.00 - 1625.00 =
  $275.00`. Same period, same transfer: B reports `$775.00` principal paid, A/C report
  `$275.00` -- a `$500.00` (= the escrow) divergence, no error raised.
- Verdict: **DIVERGE** (B-vs-A and B-vs-C hold from code; A-vs-C AGREE by construction).
- If DIVERGE: classification: SCOPE_DRIFT (A-06 escrow preprocessing absent in B),
  SILENT_DRIFT (interest base; ORM load-context for B). Inherits `monthly_payment` input risk
  only insofar as A's contractual-month rows use the engine `monthly_payment` (sites 1-8); B
  does NOT (sites 9/10 dead).
- Open questions for the developer: cross-link **A-06** (`09_open_questions.md:223-247`;
  governs whether B's raw-shadow split is acceptable for a debt account's running balance) and
  **Q-15**. No new question (A-06 already resolves that both layers apply and the un-preprocessed
  sum is incomplete -> B is a recorded SCOPE finding).

---

### Finding F-018: interest_paid_per_period consistency (A-06)

- Concept: `interest_paid_per_period` (audit-plan section 3.1 mandatory #4, part)
- Symptom link: none directly (A-06 / Schedule-A accuracy)
- Paths compared: A = `generate_schedule` raw per-row `interest`
  (`amortization_engine.py:517`); B = `_compute_mortgage_interest` calendar-year sum over
  A-06-prepared schedules (`year_end_summary_service.py:380-408`). Pair A-B; plus the two
  no-preprocessing `generate_schedule` callers as a sub-divergence.
- Path A: `amortization_engine.py:517` Read at source -- `interest = (balance *
  monthly_rate).quantize(TWO_PLACES, ROUND_HALF_UP)`; `balance` is the running schedule balance.
- Path B: `year_end_summary_service.py:401-407` Read at source -- `for schedule in
  debt_schedules.values(): for row in schedule: if row.payment_date.year == year:
  total_interest += row.interest`, where `debt_schedules` is `_generate_debt_schedules`
  (`:1453` `ctx = load_loan_context(...)`; `:1471-1483` `generate_schedule(..., payments=
  ctx.payments, rate_changes=ctx.rate_changes, anchor_balance=current_principal if ARM)`).
- Compared dimensions:
  - Effective-amount / payment input: **DIVERGES by schedule input.** Same per-row formula
    (`balance * monthly_rate`), but B's schedule is built from A-06-PREPARED payments
    (escrow-subtracted `loan_payment_service.py:305-319`, biweekly-redistributed `:321-353`).
    A raw `generate_schedule` call with un-prepared payments treats escrow as extra principal,
    paying the balance down faster and producing LOWER subsequent-month interest.
  - Period scope: A is life-of-loan per-row; B filters `payment_date.year == year`
    (`:405`) -- a calendar-year window of the same per-row series. SCOPE (by A-06 design).
  - **ORM load-context (mandatory):** B's `debt_schedules` come from `load_loan_context` which
    queries `get_payment_history` (scenario-scoped shadow income). The loan dashboard's
    schedule (`loan/_schedule.html:57`, `row.interest`) comes from `get_loan_projection` via
    `_load_loan_context` -> the SAME `load_loan_context` -> A-06-prepared. So dashboard
    per-row interest and year-end interest are consistent BY CONSTRUCTION. The divergence is
    against the two RAW callers below.
  - Quantization: A `:517` ROUND_HALF_UP; B sums those pre-quantized values
    (`:406`, no re-quantize). A-01-clean.
- Divergences:
  - Two `generate_schedule` callers bypass A-06 (Read at source this session, matching the
    A-06 verification `09_open_questions.md:243-247`): `savings_dashboard_service.py:471,488`
    (`_check_loan_paid_off`: `get_payment_history` RAW `:471`, `generate_schedule(...
    payments=confirmed)` `:488-495`, no escrow subtraction, no rate_changes) and
    `debt_strategy.py:175,181` (`_compute_real_principal`: RAW `get_payment_history` `:175`,
    `generate_schedule` `:181-190`). Their per-row interest is computed on an escrow-inflated
    paydown trajectory. They do not feed the Schedule-A mortgage-interest figure (B), but they
    drive a paid-off boolean and the debt-strategy starting principal respectively -- a
    DEFINITION divergence from the A-06-correct interest series. DEFINITION (A-06).
- Risk -- worked example: biweekly mortgage, monthly transfer `$2,400.00` (P&I `$1,900.00` +
  escrow `$500.00`), balance `$300,000.00`, `monthly_rate = 0.005`. A-06-prepared (B and
  dashboard): payment `$1,900.00`, interest `300000*0.005 = $1,500.00`, principal `$400.00`,
  next balance `$299,600.00`. RAW (`debt_strategy._compute_real_principal`,
  `_check_loan_paid_off`): payment `$2,400.00`, interest `$1,500.00`, principal `$900.00`, next
  balance `$299,100.00`; the following month's interest is `299100*0.005 = $1,495.50` vs the
  A-06-correct `299600*0.005 = $1,498.00` -- the raw path under-reports interest by `$2.50` in
  month 2 and the gap compounds. Over a calendar year this is the difference between a correct
  and an incorrect Schedule-A deduction were the raw path ever summed for it (it is not -- but
  it IS used to decide paid-off status and the debt-strategy base).
- Verdict: **DIVERGE** (the raw-vs-prepared schedule input divergence is provable from code;
  the A-B dashboard/year-end pair AGREES by construction).
- If DIVERGE: classification: DEFINITION_DRIFT (same formula, A-06-incomplete payment input in
  the two raw callers), SCOPE_DRIFT (calendar-year window, by design). Inherits the
  `monthly_payment` ARM-input risk via the contractual-month rows (F-013).
- Open questions for the developer: cross-link **A-06** (governing; both layers apply, the
  bare per-row sum is incomplete), **C-05** (`00_priors.md` C-05), W-362. No new question
  (A-06 resolves the definition; the raw-caller divergence is a recorded DEFINITION finding).

---

### Finding F-019: escrow_per_period consistency (A-06)

- Concept: `escrow_per_period` (audit-plan section 3.1 mandatory #4, part)
- Symptom link: none directly (A-06)
- Paths compared: A = `calculate_monthly_escrow` displayed
  (`escrow_calculator.py:14-57`, no `as_of` -> no inflation); B = the escrow SUBTRACTED in
  `prepare_payments_for_engine` (`loan_payment_service.py:305-319`); C =
  `loan/_escrow_list.html:37` Jinja arithmetic. Pairs A-B, A-C.
- Path A: `escrow_calculator.py:26-57` Read at source -- `total += annual/12` per active
  component (`:54-55`); inflation only when `as_of_date` AND `inflation_rate` present
  (`:35-52`); `return total.quantize(TWO_PLACES, ROUND_HALF_UP)` (`:57`). The dashboard call
  is `calculate_monthly_escrow(escrow_components)` (no `as_of`) -> no inflation.
- Path B: `loan_payment_service.py:305-319` Read at source -- `if monthly_escrow >
  Decimal("0.00"): ... new_amount = p.amount - min(monthly_escrow, p.amount -
  contractual_pi)`; `monthly_escrow` is `calculate_monthly_escrow(escrow_components)` from
  `load_loan_context:110-112` (same function, no `as_of`).
- Path C: `loan/_escrow_list.html:37` Read at source =
  `${{ "{:,.2f}".format(comp.annual_amount|float / 12) }}` -- per-component monthly escrow
  computed in Jinja with `|float` then `/ 12`.
- Compared dimensions:
  - Source / formula: A and B call the IDENTICAL function (`calculate_monthly_escrow`) with the
    same components and no `as_of` -> the displayed monthly escrow equals the per-period escrow
    the engine subtracts. AGREE on the numeric value.
  - Quantization: A `:57` ROUND_HALF_UP on the aggregate; B subtracts that aggregate. C uses
    `comp.annual_amount|float / 12` (per-component, `float`, no Decimal quantize). C is a
    different decomposition (sum-of-rounded-components vs rounded-sum) AND a coding-standards
    violation (Jinja arithmetic + `|float` on a `Numeric` -> E-16,
    `docs/coding-standards.md:163-164`).
  - ORM load-context: `escrow_components` is loaded by an explicit query in both
    `load_loan_context` (`loan_payment_service.py:104-109`) and the dashboard
    (`_load_loan_context` -> same); the inflation path requires `comp.created_at` which is a
    column, not a relationship. No load-context-dependent value divergence.
  - Period scope: A/B are the steady monthly escrow; `project_annual_escrow`
    (`escrow_calculator.py:79-115`) inflates per future year -- a different (projection)
    concept, by design, not compared here.
- Divergences:
  - A vs B: none -- same function, same inputs, same Decimal. AGREE.
  - A vs C: `loan/_escrow_list.html:37` recomputes per-component monthly escrow in Jinja
    (`comp.annual_amount|float / 12`); for components whose `annual_amount/12` does not divide
    evenly, the sum of the template's per-row rounded values can differ from the service's
    `quantize(sum(annual/12))` by a cent. The substantive finding is the **E-16
    template-computation violation** itself (cited at source), not a large numeric gap.
- Risk -- worked example: components `$1,201.00` + `$2,000.00` annual. Service A/B:
  `1201/12 + 2000/12 = 100.0833 + 166.6667 = 266.75` (`quantize` once -> `$266.75`).
  Template C: row 1 `$100.08`, row 2 `$166.67`; if any consumer sums the displayed rows it
  gets `$266.75` here but the rounding can differ by `$0.01` for other component sets, and the
  template performs the division in `float` (E-16). The escrow the engine subtracts (`$266.75`)
  equals the dashboard's `monthly_escrow` (`$266.75`) -- consistent.
- Verdict: **AGREE** on the numeric escrow-per-period (display A == engine-subtracted B, proven
  by shared function + identical inputs). The `loan/_escrow_list.html:37` Jinja-arithmetic /
  `|float` is recorded as an **E-16 coding-standards finding** (template computes a money
  value), not a numeric drift.
- If DIVERGE: n/a for the numeric value. The E-16 row is a Phase-6/standards finding, recorded
  here for the cross-link.
- Open questions for the developer: none. Cross-link A-06 (escrow-subtraction layer),
  W-201/W-202, and the 1.3 template-arithmetic inventory row (`_escrow_list.html:37`).

---

### Finding F-020: total_interest consistency (two definitions, A-06)

- Concept: `total_interest` (audit-plan section 3.1 mandatory #4, part)
- Symptom link: inherits #2/#4 via the schedule's `monthly_payment` inputs (F-013)
- Paths compared: A = `_derive_summary_metrics` life-of-loan
  (`amortization_engine.py:642-644`); B = `_compute_mortgage_interest` calendar-year, A-06
  (`year_end_summary_service.py:380-408`); C = `calculate_strategy` per-debt total
  (`debt_strategy_service.py:521`). Pairs A-B, A-C, B-C.
- Path A: `amortization_engine.py:642-644` Read at source -- `total_interest = sum((row.interest
  for row in schedule), Decimal("0.00"))`; consumed via `get_loan_projection` ->
  `summary.total_interest`, rendered `loan/dashboard.html:139` "Total Interest (life of loan)".
- Path B: `year_end_summary_service.py:401-407` (see F-018) -- calendar-year sum over
  A-06-prepared schedules.
- Path C: `debt_strategy_service.py:600-415,679` -- `_accrue_interest`
  (`:411-413`, `balance * interest_rate / TWELVE` ROUND_HALF_UP) accumulated into
  `interest_totals[i]` (`:415`), returned as `total_interest` per debt (`:679`).
- Compared dimensions:
  - Definition: A = life-of-loan (every schedule row); B = ONE calendar year of the
    A-06-prepared schedule; C = the strategy walk's accumulated monthly interest from
    `DebtAccount.current_principal` (= `real_principal`, site 16) at `start_date=today`. A and
    B are different SCOPES of the same per-row formula (DEFINITION-by-design, governed by A-06
    - W-045); C is a separate amortization implementation.
  - Principal/start base: A walks from `original_principal` (or anchor for ARM); C starts from
    `real_principal` (`debt_strategy.py:139`) at today. Different starting balances ->
    different total interest for the same loan.
  - **ORM load-context (mandatory):** A's schedule comes from the caller's
    `payments`/`rate_changes` (A-06-prepared via `_load_loan_context`/`_generate_debt_schedules`,
    or RAW/None via other callers, F-018). C's `real_principal` for a FIXED loan comes from
    `_compute_real_principal`'s own RAW `get_payment_history` (`debt_strategy.py:175`). So A
    (dashboard, A-06) and C (strategy, raw replay) consume different payment inputs even before
    the formula difference. SILENT/SCOPE.
  - Quantization: A sums pre-quantized `row.interest` (`amortization_engine.py:517`,
    ROUND_HALF_UP); C `_accrue_interest` quantizes each month ROUND_HALF_UP
    (`debt_strategy_service.py:411-413`). A-01-clean. The payoff-route
    `committed_interest_saved` is the A-01 site (F-021), not this concept.
  - Period scope: A life-of-loan; B one year; C from today to per-debt payoff. By design.
- Divergences:
  - A vs B: different scope (life-of-loan vs one calendar year). DEFINITION-by-design; a page
    that labels B as "total interest" without the year qualifier would conflate them. Recorded
    per A-06 (both apply); the dashboard "(life of loan)" label (`loan/dashboard.html:139`) and
    the year-end tab figure are distinct quantities -- HOLDS as long as labels stay distinct.
  - A vs C: life-of-loan engine total from `original_principal`/anchor vs strategy total from
    `real_principal` at today; for the same single loan + same extra these MUST reconcile
    (a one-debt strategy run == the payoff calculator) and they need not, because the start
    balance and minimum-payment derivation differ (C uses site 16; A uses sites 1-8). DEFINITION.
  - B vs C: B A-06-prepared engine schedule, C raw-replay strategy walk -- diverge as A-vs-C
    plus the raw/prepared payment input. SCOPE.
- Risk -- worked example: fixed mortgage, `original_principal $300,000`, confirmed payments
  walked engine balance to `$290,000`, escrow-inclusive raw replay over-counts to `$289,500`.
  Dashboard "Total Interest (life of loan)" (A) = `sum(row.interest)` over the A-06-prepared
  full-life schedule from `$300,000`. Debt-strategy (C) accrues interest from `$289,500`
  (raw real_principal) over its own month-loop at `start_date=today` -> a strictly different
  total. Same loan, two "total interest" numbers; the user comparing the loan dashboard to the
  debt-strategy results sees an unlabeled difference.
- Verdict: **DIVERGE** (A-vs-C and B-vs-C hold from code; A-vs-B is DEFINITION-by-design and
  HOLDS while labels remain distinct).
- If DIVERGE: classification: DEFINITION_DRIFT (life-of-loan vs calendar-year vs strategy-total;
  start-base difference), SCOPE_DRIFT (A-06 raw-vs-prepared for C), inherits SILENT
  `monthly_payment` input risk (F-013).
- Open questions for the developer: cross-link A-06, C-05, W-293/W-362, and **Q-09**
  (the `debt_strategy.py:127` site-16 base that C is built on). No new question.

---

### Finding F-021: interest_saved consistency

- Concept: `interest_saved`
- Symptom link: inherits #2/#4 via `monthly_payment` (F-013)
- Paths compared: A = `calculate_summary` standard-vs-accelerated
  (`amortization_engine.py:740,749`); B = the loan-route committed-vs-original
  (`loan.py:960-968`); C = `calculate_strategy` strategy-vs-minimum
  (`debt_strategy_service.py`). Pairs A-B, A-C, B-C.
- Path A: `amortization_engine.py:740,749` Read at source -- `interest_saved =
  total_interest_standard - total_interest_extra` (`:740`),
  `interest_saved.quantize(TWO_PLACES, ROUND_HALF_UP)` (`:749`). A-01-clean.
- Path B: `app/routes/loan.py:960-968` Read at source -- `original_interest =
  sum(r.interest for r in original_schedule)` (`:960-962`), `committed_interest = sum(...)`
  (`:963-965`), `committed_interest_saved = (original_interest -
  committed_interest).quantize(Decimal("0.01"))` (`:966-968`) -- **NO `rounding=ROUND_HALF_UP`
  -> defaults to `ROUND_HALF_EVEN` (banker's)**. `loan.py:968` is EXACTLY in the A-01
  24-omission list (`09_open_questions.md:42-48`, "`loan.py:968`"); the A-01 verification
  verdict is PARTIALLY ACCURATE and names this site.
- Path C: `debt_strategy_service.py` -- per-debt `interest_totals` differenced between the
  baseline (`extra=0`) `calculate_strategy` run and the avalanche/snowball run
  (`debt_strategy.py:362-372`).
- Compared dimensions:
  - Quantization: **DIVERGES.** A `ROUND_HALF_UP` (`amortization_engine.py:749`); B
    `.quantize(Decimal("0.01"))` with default `ROUND_HALF_EVEN` (`loan.py:968`, the A-01 site);
    C is a difference of ROUND_HALF_UP-accrued totals (`debt_strategy_service.py:411-413`). For
    a half-cent boundary A and B round in opposite directions. ROUNDING (A-01).
  - Definition: A = standard (no extra) vs accelerated (extra) of the SAME schedule; B =
    contractual ORIGINAL (no payments) vs COMMITTED (confirmed+projected payments, no extra) --
    a different pair of schedules answering "interest saved by my payment behavior" not "by an
    extra payment". C = strategy ordering vs minimum-only across multiple debts. Three
    different "interest saved" quantities sharing one token.
  - Principal/payment input: A/B build schedules via the engine with A-06-prepared `payments`
    (`loan.py` `_load_loan_context`); C starts from site-16 `real_principal` (raw replay for
    fixed). SCOPE (A-06) for B-vs-C.
  - ORM load-context: B's `original_schedule` is generated with `payments` omitted
    (`loan.py:917-923`, contractual baseline by design); its `committed_schedule` with
    A-06-prepared payments (`:925-935`). The saved figure depends on the caller having built
    both; consistent within the route. C depends on `_compute_real_principal`'s own query.
  - Status/scenario/is_deleted: engine paths share `get_payment_history` filters; not the axis.
- Divergences:
  - A vs B quantization: `ROUND_HALF_UP` (`amortization_engine.py:749`) vs default banker's
    (`loan.py:968`). ROUNDING (A-01-confirmed site).
  - A/B vs C: different definition (single-loan acceleration vs payment-behavior vs cross-debt
    strategy) and different start base (engine schedule vs site-16 real_principal). DEFINITION.
  - A vs B: different schedule pair (standard/accelerated vs original/committed). DEFINITION.
- Risk -- worked example: a loan where `original_interest - committed_interest =
  $1,234.565`. Path B `loan.py:968` `.quantize(Decimal("0.01"))` (banker's) -> `$1,234.56`
  (rounds to even). Path A `amortization_engine.py:749` `ROUND_HALF_UP` -> `$1,234.57`. The
  `_payoff_results.html:19` "interest saved" and any A-derived figure for the same loan differ
  by `$0.01` at the half-cent boundary; over the A-01 verification's 24-site pattern this is
  the documented banker's-default class. The DEFINITION gap (A vs C) is dollars, not cents:
  a single-debt avalanche run's interest_saved need not equal the payoff calculator's because
  C starts from `real_principal` at today while A starts from the schedule.
- Verdict: **DIVERGE.**
- If DIVERGE: classification: ROUNDING_DRIFT (A-01 site `loan.py:968`, banker's vs HALF_UP),
  DEFINITION_DRIFT (three different "interest saved" definitions; start-base difference),
  SCOPE_DRIFT (A-06 for C).
- Open questions for the developer: cross-link the **A-01 verification** verdict
  (`09_open_questions.md:37-62`, `loan.py:968` is listed) and Gate D
  (`02_concepts.md:3164-3194`). No new question -- A-01 is a resolved developer answer; the
  omission at `loan.py:968` is a recorded ROUNDING finding.

---

### Finding F-022: months_saved consistency

- Concept: `months_saved`
- Symptom link: inherits #2/#4 via schedule length (F-013)
- Paths compared: A = `calculate_summary` `len(standard) - len(accelerated)`
  (`amortization_engine.py:739`); B = loan-route `len(original_schedule) -
  len(committed_schedule)` (`loan.py:957-959`); C = refinance `break_even_months`
  (`loan.py:1136-1145`, W-242 distinct formula); D = `calculate_strategy` per-debt
  `payoff_months` (`debt_strategy_service.py:632-634`). Pairs A-B, A-C, A-D, B-C, B-D, C-D.
- Path A: `amortization_engine.py:739` Read at source -- `months_saved = len(standard) -
  len(accelerated)`.
- Path B: `loan.py:957-959` Read at source -- `committed_months_saved =
  len(original_schedule) - len(committed_schedule)` (committed vs contractual original, NOT
  standard vs accelerated).
- Path C: `loan.py:1136-1145` Read at source -- `break_even_months =
  int((closing_costs / monthly_savings).to_integral_value(rounding=ROUND_CEILING))` when
  `closing_costs > 0 and monthly_savings > 0` -- a refinance break-even, NOT a schedule-length
  difference; reuses the render slot (W-242).
- Path D: `debt_strategy_service.py:632-634` -- `payoff_months[i]` per debt; "months saved" is
  the baseline-vs-strategy difference of these.
- Compared dimensions:
  - Definition: A standard-vs-accelerated; B committed-vs-original; C closing-cost break-even
    (a ratio, not a length diff); D strategy payoff-month delta. Four different integer-month
    quantities, one token.
  - Units / quantization: A/B/D integer = difference of two `len()`; C
    `to_integral_value(ROUND_CEILING)` of a money ratio -- NOT an A-01 money quantize (it is a
    month count; ROUND_CEILING is intentional "round up to the next whole month", consistent
    with `savings_goal_service.py:462-463`'s documented-intentional ceiling per the A-01
    verification, `09_open_questions.md:49-51`).
  - Schedule input: A/B engine schedules built from A-06-prepared `payments`
    (`loan.py:_load_loan_context`); the contractual baselines (`:917-923` original, `:453-459`)
    intentionally omit payments. D from site-16 `real_principal`. SCOPE for B/D.
  - ORM load-context: A/B depend on the caller building both schedules with the right
    payments; the `original_schedule` deliberately omits payments (contractual). Consistent
    within the route.
- Divergences:
  - A vs B: standard/accelerated vs original/committed -- different question; can differ by
    many months for the same loan. DEFINITION.
  - A/B vs C: schedule-length difference vs closing-cost break-even ratio -- not the same
    quantity; rendering both as "months" risks misleading the user (W-242 requires C be
    labelled distinctly). DEFINITION.
  - A vs D: engine vs strategy-walk payoff-month delta; differ because D starts from
    `real_principal` at today with site-16 minimum payment. DEFINITION.
- Risk -- worked example: a refinance where `closing_costs = $4,000.00`,
  `monthly_savings = $150.00` -> C `break_even_months = ceil(4000/150) = 27`. The same loan's
  acceleration with `extra = $200/mo` shaves A `months_saved = len(standard) -
  len(accelerated) = 54`. Both render in a "months" slot; `27` (break-even) and `54`
  (acceleration) measure entirely different things. A user reading "27 months" on the
  refinance card and "54 months" on the payoff card for the same loan can reasonably but
  wrongly compare them.
- Verdict: **DIVERGE** (definitional fork; P2-b override of 1.7.4's single-path
  classification, `02_concepts.md:1145-1151`).
- If DIVERGE: classification: DEFINITION_DRIFT (four distinct month quantities sharing the
  token; render-slot reuse for the refinance break-even).
- Open questions for the developer: none new. Cross-link W-242 and the audit-plan section-9
  intent (verify each "months" figure is labelled so the user is not misled). Inherits the
  `monthly_payment` input risk (F-013) for A/B schedule lengths.

---

### Finding F-023: payoff_date consistency

- Concept: `payoff_date`
- Symptom link: inherits #2/#4 via the schedule last-row date (F-013)
- Paths compared: A = `_derive_summary_metrics` `schedule[-1].payment_date`
  (`amortization_engine.py:645`); B = `calculate_payoff_by_date` inverse
  (`amortization_engine.py:753-845`); C = `calculate_strategy` per-debt payoff
  (`debt_strategy_service.py`); D = W-239 transfer-template `end_date` auto-sync
  (`loan.py:513-516`). Pairs A-B, A-C, A-D, B-C.
- Path A: `amortization_engine.py:640-645` Read at source -- `payoff_date =
  schedule[-1].payment_date` (fallback `fallback_date` when empty `:640-641`); consumed via
  `get_loan_projection` -> `summary.payoff_date`, rendered `loan/dashboard.html:143`.
- Path B: `amortization_engine.py:753-845` Read at source -- `calculate_payoff_by_date`
  signature has **no `payments`, `anchor_balance`, or `anchor_date` parameters** (`:753-762`);
  it `generate_schedule(... )` WITHOUT payment replay or anchor (`:779-786`, `:824-832`). The
  loan route calls it (`loan.py:1000-1009`) with `current_principal=real_principal`
  (`= proj.current_balance`, `:998`) and `origination_date=date.today().replace(day=1)`
  (`:1005`) -- a today-forward what-if, not the committed schedule.
- Path C: `debt_strategy_service.py` -- per-debt `payoff_months` -> date via `start_date=today`
  (`debt_strategy.py:362-372`).
- Path D: `loan.py:513-516` `_update_transfer_end_date(existing_template, summary,
  proj.schedule, account.id)` -- syncs the recurring transfer template's `end_date` to the
  projected payoff (W-239).
- Compared dimensions:
  - Source: A reads the committed-schedule last row (A-06-prepared payments, ARM anchor); B
    reads a schedule built WITHOUT payments/anchor from `real_principal` at a today origination;
    C from the strategy walk at today; D consumes A's `summary`/`proj.schedule`.
  - **ORM load-context (mandatory): DIVERGES.** A's payoff is the last row of the schedule the
    caller built (A-06-prepared via `_load_loan_context` on the dashboard). B explicitly
    cannot take `payments`/`anchor` (signature `:753-762`), so for an ARM B ignores the
    user-verified anchor and projects origination/today-forward -> a different last-row date
    than A for the same ARM. SILENT.
  - Definition: A = committed schedule end; B = the date achievable with the binary-searched
    extra payment (`:818-845`); C = strategy-ordering payoff; D = A re-used to bound shadow
    generation. A and D agree by construction (D consumes A). B and C are different questions.
  - Quantization: dates, not money. Not applicable.
- Divergences:
  - A vs B for an ARM: B (`calculate_payoff_by_date`) has no `anchor_balance` parameter
    (`amortization_engine.py:753-762`); it cannot reproduce A's anchored ARM schedule, so the
    target-date what-if's implied payoff trajectory diverges from the displayed
    `summary.payoff_date`. SILENT.
  - A vs C: committed engine schedule vs strategy walk from site-16 `real_principal` at today
    -> different last-payoff date for the same loan. DEFINITION/SCOPE.
  - Both inherit F-013: divergent `monthly_payment` inputs shift the last-row date.
- Risk -- worked example: ARM, anchored `current_balance $280,000`, stored rate `6.5%`,
  `remaining 300`. A (`summary.payoff_date`) = last row of the anchored committed schedule ~=
  `2049-08`. The user opens the payoff "target date" tab; B
  (`calculate_payoff_by_date`, `loan.py:1000-1009`) builds schedules from `real_principal =
  $280,000` with `origination_date = today` and NO anchor (it cannot take one) -- for this ARM
  the resulting standard payoff is a different month than A because the pre-anchor rows and
  rate handling differ. The displayed projected payoff (`loan/dashboard.html:143`) and the
  target-date tool's baseline payoff disagree for the same loan.
- Verdict: **DIVERGE** (A-vs-B ORM/anchor seam and A-vs-C definition hold from code; A-vs-D
  AGREE by construction -- D consumes A).
- If DIVERGE: classification: SILENT_DRIFT (B cannot take payments/anchor -> ARM mismatch),
  DEFINITION_DRIFT (strategy vs engine), inherits F-013 SILENT.
- Open questions for the developer: cross-link W-239 (D == displayed payoff -- verify), A-05
  (`monthly_payment` input divergence shifts the date). No new question; the
  `calculate_payoff_by_date` no-anchor scope is a recorded SILENT finding.

---

### Finding F-024: loan_remaining_months internal-consistency verify (single-path)

- Concept: `loan_remaining_months` (E1 single-path scoped internal-verify,
  `02_concepts.md:3259-3261`)
- Symptom link: none directly (it is the shared `n` that makes the F-013 invariant tractable)
- Paths compared: single canonical producer `calculate_remaining_months`
  (`amortization_engine.py:128-142`); scoped internal check that every ARM `monthly_payment`
  site derives `n` from it with `as_of=today`, plus the one consumer that uses a DIFFERENT
  "remaining months" derivation.
- Path (producer): `amortization_engine.py:136-142` Read at source -- `if as_of is None:
  as_of = date.today()`; `months_elapsed = (as_of.year - origination_date.year)*12 +
  (as_of.month - origination_date.month)`; `return max(0, term_months - months_elapsed)`.
  Pure calendar formula, payment-count-independent, default `as_of=today`.
- Internal-consistency check (Read at source): sites 7 (`:908-910`), 9
  (`balance_calculator.py:222-224`), 11 (`loan_payment_service.py:247-249`), 14
  (`loan.py:1222-1224`) all call `calculate_remaining_months(origination_date, term_months)`
  with no `as_of` -> `today`. `n` is therefore IDENTICAL across these sites for the same
  loan-on-date; F-013's `monthly_payment` fluctuation is attributable to principal/rate (and
  the sites-3/4 loop-index `n`), NOT to this concept. **HOLDS.**
- One consumer uses a different "remaining months": `loan.py:1126`
  `current_remaining_months = len(current_schedule)` (refinance comparison) -- a schedule
  ROW COUNT, not `calculate_remaining_months`. For a partially-paid loan
  `len(current_schedule)` (rows until balance hits 0 under committed payments) differs from
  the calendar `term_months - months_elapsed`. This is a DEFINITION note: the refinance
  "remaining months" and the engine `remaining_months` are different quantities; the refinance
  template (`loan/_refinance_results.html:51`) must not be read as the calendar remaining.
- Compared dimensions: producer is a pure function of `(origination_date, term_months,
  as_of=today)`; no status/scenario/effective-amount/quantization axis. ORM load-context: none
  (operates on scalar columns). The only divergence is the `loan.py:1126` `len(schedule)`
  alternative definition.
- Divergences: none in the canonical producer (single implementation, every ARM site calls
  it). The `loan.py:1126` `len(current_schedule)` is a separate "months until payoff under
  committed payments" quantity, correctly distinct by design; recorded so P3-reconcile sees
  the single-path verify did not skip the consumer.
- Verdict: **AGREE** (single canonical producer `amortization_engine.py:128-142`; every ARM
  `monthly_payment` site uses it with `as_of=today`; internally consistent). The
  `loan.py:1126` schedule-length "remaining" is a labelled-distinct consumer quantity, not a
  drift in this concept.
- If DIVERGE: n/a.
- Open questions for the developer: none. (Phase 7 test-gap: `calculate_remaining_months` has
  zero pinned-value coverage per PA-28, `02_concepts.md:3346`; recorded for `07_test_gaps.md`.)

---

### Finding F-025: dti_ratio consistency (debt side)

- Concept: `dti_ratio` (debt side; income denominator is P2-c)
- Symptom link: #5 (debt card vs dashboard widget); cross-references **F-008** (co-displayed
  `total_debt` base)
- Paths compared: A = `savings/dashboard.html` DTI via `compute_dashboard_data`
  (`savings_dashboard_service.py:61`, division `:173-176`); B = `dashboard/_debt_summary.html`
  DTI via `dashboard_service._get_debt_summary` delegate (`dashboard_service.py:533`); plus
  the internal numerator-vs-co-displayed-total base check inside `_compute_debt_summary`.
  Pair A-B (must be byte-identical -- one delegates to the other).
- Path A: `savings_dashboard_service.py:168-179` Read at source --
  `gross_monthly = (gross_biweekly * Decimal("26") / Decimal("12")).quantize(_TWO_PLACES,
  ROUND_HALF_UP)` (`:170-172`); `dti_ratio = (debt_summary["total_monthly_payments"] /
  gross_monthly * Decimal("100")).quantize(Decimal("0.1"), ROUND_HALF_UP)` (`:173-176`);
  `total_monthly_payments` from `_compute_debt_summary` `:851-856` (`monthly_total =
  (monthly_pi + monthly_escrow).quantize(_TWO_PLACES, ROUND_HALF_UP)` `:851-853`;
  `monthly_pi = ad["monthly_payment"]` `:846` = engine `proj.summary.monthly_payment`).
- Path B: `dashboard_service._get_debt_summary` (`:533`, delegate-only per 1.7.8 operational
  rule, `02_concepts.md:592-597`) -> `return savings_dashboard_service.compute_dashboard_data(
  ...)["debt_summary"]` -- consumes A's output unchanged. A P1-f-relocated CONSUMER, NOT a
  comparable producer path.
- Compared dimensions:
  - Producer: single producer (`savings_dashboard_service`); B delegates. **AGREE by
    construction** -- B cannot diverge numerically because it returns A's dict. The Phase-3
    check is that the delegation introduces no divergence (no separate user-scoping/caching):
    Read confirms `_get_debt_summary` is `return ...compute_dashboard_data(user_id)[
    "debt_summary"]` with the same `user_id`. HOLDS.
  - Quantization: `gross_monthly` ROUND_HALF_UP (`:170-172`), `dti_ratio` ROUND_HALF_UP 1dp
    (`:173-176`), `monthly_total` ROUND_HALF_UP (`:851-853`) -- A-01-clean. NOTE the income
    BASE `salary_gross_biweekly` at `:263-266` uses `.quantize(Decimal("0.01"))` with default
    banker's (A-01 24-list site `:266`, Gate F1 `02_concepts.md:3358`) -- that is the P2-c
    `paycheck_gross` seam, cross-linked, NOT re-litigated here.
  - **Internal base inconsistency (holds regardless of any Q):** the DTI numerator uses
    `monthly_pi = ad["monthly_payment"]` (`:846`, engine-derived) while the co-displayed
    `total_debt` on the SAME card uses `principal = Decimal(str(lp.current_principal))`
    (`:840`, STORED). One service, one loan, two principal bases on one card -- the DTI ratio
    is monthly-payment-based (internally consistent) but `total_debt` beside it is the stored
    base (cross-ref **F-008**'s internal-inconsistency divergence).
  - ORM load-context: `monthly_pi` flows from `_compute_account_projections`
    (`get_loan_projection` with `load_loan_context` payments); `total_debt` reads the stored
    `lp.current_principal` column. The numerator is load-context-dependent (engine), the
    co-displayed total is not (stored) -- the same F-008/F-014 stored-vs-engine seam.
  - Status/scenario/is_deleted: `_compute_debt_summary` operates on `account_data` already
    filtered by `_compute_account_projections`; not the divergence axis.
- Divergences:
  - A vs B: none numerically (B delegates to A; Read-confirmed `dashboard_service.py:533` ->
    `compute_dashboard_data(...)["debt_summary"]`). AGREE.
  - Internal: DTI numerator (engine `monthly_pi`, `:846`) vs co-displayed `total_debt`
    (STORED `current_principal`, `:840`) -- not the DTI value itself but the card's two
    figures rest on different principal bases (F-008). SOURCE (cross-ref).
  - Q-12 mortgage double-count: a mortgage counted in `/obligations` `total_expense_monthly`
    AND the DTI numerator -- no reconciliation guard (A-12 proposed, pending).
- Risk -- worked example: stored `current_principal = $300,000` (used for the card's
  `total_debt`), engine `monthly_pi = $1,899.00`, `monthly_escrow = $500.00`,
  `gross_biweekly = $3,000.00`. `gross_monthly = 3000 * 26 / 12 = $6,500.00`;
  `total_monthly_payments = (1899.00 + 500.00) = $2,399.00`;
  `dti_ratio = 2399 / 6500 * 100 = 36.9%`. The dashboard widget (B) shows the SAME `36.9%`
  (delegate). But the card's `total_debt` shows `$300,000` (stored) while the same loan's
  account card elsewhere shows `proj.current_balance` (engine) -- the DTI itself is consistent
  A-vs-B; the F-008 base inconsistency is the live divergence on the card.
- Verdict: **AGREE** for the A-vs-B DTI value (delegation introduces no divergence,
  Read-confirmed). The co-displayed `total_debt` base inconsistency is a **cross-reference to
  F-008** (recorded there as DIVERGE, UNKNOWN on Q-15), not re-verdicted here. The Q-12
  double-count risk is **UNKNOWN, blocked on Q-12**.
- If DIVERGE: n/a for A-vs-B. The F-008 cross-ref carries SOURCE_DRIFT; Q-12 carries the
  double-count.
- Open questions for the developer: **Q-12** (`09_open_questions.md:459-512`; obligations /
  DTI mortgage double-count, 26/12 duplication -- A-12 proposed, pending), **Q-15** (F-008
  co-displayed base). Cross-link Gate F1 (`02_concepts.md:3358`, the `:266` banker's income
  base = P2-c seam), W-247, W-297, **F-008**.

---

### Finding F-026: 5/5 ARM payment stability inside the fixed-rate window -- symptom #4 (hand computation)

- Concept: `monthly_payment` -- E-02 invariant (audit-plan section 3.1 mandatory #3; standalone
  symptom-#4 finding, analogous to P3-a F-009)
- Symptom link: **#4** (5/5 ARM monthly payment fluctuating by a few dollars over consecutive
  months despite being inside the fixed-rate window)
- E-02 (`00_priors.md:166-170`): "A 5/5 ARM during the first five years has one monthly payment
  value ... however many times it is called and from whichever entry point ... Fluctuation by
  even a few cents is a finding."
- Paths compared: the displayed Monthly P&I site 7 (`amortization_engine.py:952-954`) across
  consecutive calendar months for the SAME 5/5 ARM with NO rate change and NO manual edit
  inside the 60-month fixed window; compared to the hand-computed correct constant payment.
- Structural root cause (Read at source this session): `LoanParams.arm_first_adjustment_months`
  / `arm_adjustment_interval_months` (`loan_params.py:60-61`) are STORED, form-bound
  (`loan.py:670`), schema-validated (`validation.py:1450-1451`), and consumed by **zero**
  calculation sites (`grep` this session: only model / `_PARAM_FIELDS` / schema). The
  amortization engine has **no representation of the fixed-rate window**. Site 7
  (`amortization_engine.py:952-954`) computes
  `calculate_monthly_payment(current_principal, rate, remaining)` where
  `remaining = calculate_remaining_months(origination_date, term_months)` (`:908-910`) =
  `max(0, term_months - months_elapsed)` -- `months_elapsed` increases by 1 every calendar
  month -- and `current_principal = Decimal(str(params.current_principal))` (`:913`) is the
  STORED column, which (F-014, grep-proven) is NOT reduced as transfers settle (only
  `update_params` writes it).
- The amortization identity (why a correct ARM payment is constant in the fixed window): for a
  fully-amortizing loan at fixed monthly rate `i`, the level payment `M` that amortizes
  principal `P0` over `N` months satisfies `M = P0 * i(1+i)^N / ((1+i)^N - 1)`. After `k`
  scheduled payments the TRUE remaining balance is `B_k`, and
  `calculate_monthly_payment(B_k, i, N-k)` returns **exactly `M` again** -- re-amortizing the
  *true* remaining balance over the *remaining scheduled* term reproduces the same payment.
  The payment is constant in the fixed window **iff** the principal fed to the formula is the
  true amortized balance for that shrinking term.
- Hand computation (the actual arithmetic). Take a 5/5 ARM: `original_principal = $400,000.00`,
  `interest_rate = 0.06000` (6.000%, monthly `i = 0.06/12 = 0.005`), `term_months = 360`,
  `origination_date = 2024-02-15`, `is_arm = True`, `arm_first_adjustment_months = 60` (rate
  fixed for 60 months), no RateHistory rows inside the window. Stored
  `current_principal = $400,000.00` (developer has not manually edited it; per symptom #3 /
  F-014 settled transfers do not reduce it).
  - **Correct constant payment** (origination, `N = 360`):
    `(1.005)^360`: `ln(1.005) = 0.00498754`; `* 360 = 1.79551`; `e^1.79551 = 6.022575`.
    `M = 400000 * 0.005 * 6.022575 / (6.022575 - 1) = 400000 * 0.030112875 / 5.022575
    = 12045.15 / 5.022575 = $2,398.20` (rounded HALF_UP, `amortization_engine.py:197`).
    Per E-02 this single value must hold for all 60 months of the fixed window.
  - **What site 7 returns at `months_elapsed = 24`** (`remaining = 360 - 24 = 336`), with the
    FROZEN stored principal `$400,000.00`:
    `(1.005)^336`: `0.00498754 * 336 = 1.67581`; `e^1.67581 = 5.34355`.
    `M = 400000 * 0.005 * 5.34355 / (5.34355 - 1) = 400000 * 0.026717750 / 4.34355
    = 10687.10 / 4.34355 = $2,460.45`.
  - **What site 7 returns at `months_elapsed = 25`** (`remaining = 335`):
    `0.00498754 * 335 = 1.67082`; `e^1.67082 = 5.31693`.
    `M = 400000 * 0.005 * 5.31693 / (5.31693 - 1) = 400000 * 0.026584650 / 4.31693
    = 10633.86 / 4.31693 = $2,463.27`.
  - Month 24 -> month 25 the displayed Monthly P&I moves `$2,460.45 -> $2,463.27` (**+$2.82**),
    inside the fixed-rate window, with NO rate change and NO manual edit. Both differ from the
    correct constant `$2,398.20`. The payment drifts UPWARD every month because the formula
    re-amortizes a NON-decreasing principal (`$400,000`, frozen because symptom #3 means
    settled transfers do not reduce the stored column) over a STRICTLY DECREASING`remaining`.
    The arithmetic above is the explicit demonstration; the few-dollar month-over-month delta
    is exactly the developer's reported symptom-#4 shape.
- Compared dimensions:
  - `n` source: site 7 `n = calculate_remaining_months` shrinks by 1 monthly
    (`amortization_engine.py:138-142`). This is THE drift driver here.
  - Principal source: STORED `current_principal` (`:913`), frozen (F-014). If it tracked the
    true amortized balance `B_k`, the identity would hold and the payment would be constant.
  - Rate source: STORED `interest_rate` (`:914`), constant inside the window (no RateHistory)
    -- so rate is NOT the driver for the pure in-window case; it compounds the drift only when
    a RateHistory row or manual rate edit exists (F-013 site-4 axis).
  - ORM load-context: site 7's scalar does not depend on `payments` (it reads the stored
    column); the SCHEDULE per-row payment (sites 3/4) does -- so the dashboard "Monthly P&I"
    and the schedule rows can ALSO disagree (F-013), a second face of symptom #4.
  - Quantization: `:197` ROUND_HALF_UP, A-01-clean -- not the driver (the drift is dollars,
    not sub-cents).
- Divergences: site 7 evaluated on consecutive months yields different Decimals
  (`$2,460.45` vs `$2,463.27` above) for one 5/5 ARM inside its fixed window -- a direct E-02
  violation, root-caused at `amortization_engine.py:952-954` consuming a calendar-shrinking
  `remaining` (`:908-910`) against a frozen STORED `current_principal` (`:913`). The engine
  has no `arm_first_adjustment_months` awareness to hold the payment constant
  (`loan_params.py:60-61` inert, grep-proven).
- Risk: every page that shows an ARM's Monthly P&I (loan dashboard `loan/dashboard.html:129`,
  the recurring-transfer prefill `loan.py:1225-1229` site 14, the debt-summary PITI
  `savings_dashboard_service.py:846`) recomputes site-7-equivalent on each request, so the
  number a user sees creeps upward month over month and the recurring-transfer auto-amount
  drifts with it. This is symptom #4, and it is the same mechanism as symptom #2 (F-013) with
  the fixed-window lens; it is downstream of symptom #3 (F-014) because if the stored
  `current_principal` were maintained equal to the true amortized balance the identity would
  restore the constant payment.
- Verdict: **DIVERGE** -- E-02 violated; provable from code + the hand arithmetic above. The
  *intended fix shape* (maintain stored `current_principal` on settle vs re-amortize from
  `proj.current_balance` vs derive `n` from a fixed-window reset) requires a developer
  decision -> the remediation is gated, NOT the verdict.
- If DIVERGE: classification: SILENT_DRIFT (an unlabeled few-dollar monthly creep on a
  primary budgeting figure, no error raised), with PLAN_DRIFT against W-048
  (`00_priors.md:295`, ARM method `planned-per-plan`) and E-02.
- Open questions for the developer: **Q-17** (filed in `09_open_questions.md` this session):
  for a 5/5 ARM inside the fixed-rate window, must site 7
  (`amortization_engine.py:952-954`) re-amortize the *engine-real* current balance
  (`proj.current_balance`) -- or hold the payment constant via `arm_first_adjustment_months`
  -- rather than re-amortizing the FROZEN stored `current_principal` over a shrinking
  `remaining`? Cross-link **Q-15**/**Q-09** (the dispatcher / site-16 questions that bound the
  fix), A-04/A-05 verification, E-02, **F-013**, **F-014**.

---

### P3-b verification (a-f)

- **(a) E1 loan/debt rows reconciled.** E1 loan/debt rows = 13 (`monthly_payment`,
  `loan_principal_real`, `loan_principal_stored`, `loan_principal_displayed`,
  `principal_paid_per_period`, `interest_paid_per_period`, `escrow_per_period`,
  `total_interest`, `interest_saved`, `months_saved`, `payoff_date`, `dti_ratio` +
  single-path `loan_remaining_months`); findings F-013..F-025 = 13, mapped 1:1 in the table
  above. F-026 is the standalone section-3.1 #3 (symptom #4). **E1 loan/debt rows: 13;
  findings produced: 13 (+F-026 standalone). HOLDS.**
- **(b) Four mandatory section-3.1 comparisons + symptoms #2/#3/#4 each have a worked
  finding.** #1 real-vs-stored-vs-displayed (symptom #3) -> F-014 (worked: ARM $312,000 card
  never moves; fixed $312,000 -> engine $310,847.12 vs card $312,000). #2 16-site
  monthly_payment pairwise (symptom #2) -> F-013 (worked: site-7 vs site-3 `n` off-by-one ->
  $2,628 vs $2,631; "$1910.95 after edit" = site-7 with new stored principal). #3 5/5-ARM
  hand computation (symptom #4) -> F-026 (explicit arithmetic: $2,398.20 correct vs $2,460.45
  month 24 vs $2,463.27 month 25). #4 total_interest/interest_paid_per_period/escrow_per_period
  A-06 -> F-018/F-019/F-020 (worked escrow-inclusive examples). **5/5 present. HOLDS.**
- **(c) The 5/5-ARM hand computation shows actual arithmetic, not an assertion.** F-026
  computes `(1.005)^360 = 6.022575`, `M = 400000*0.005*6.022575/5.022575 = $2,398.20`, then
  `(1.005)^336 = 5.34355 -> $2,460.45` and `(1.005)^335 = 5.31693 -> $2,463.27`, with the
  amortization-identity explanation. **HOLDS.**
- **(d) Every DIVERGE has a concrete worked example.** F-013 ($2,628/$2,631 + $1910.95-after-
  edit), F-014 ($312,000 ARM frozen / $310,847.12 fixed), F-015 ($250,000 stored vs
  $248,910.44 engine), F-016 ($300,000 / $297,450 / $297,180 three-page), F-017 ($775 vs $275
  escrow split), F-018 ($1,498 vs $1,495.50 month-2 interest), F-020 ($300,000 vs $289,500
  base), F-021 ($1,234.56 vs $1,234.57 banker's/HALF_UP), F-022 (27 break-even vs 54
  acceleration), F-023 (ARM anchored vs no-anchor payoff month), F-026 (above). UNKNOWN-verdict
  findings (F-016 Q-11) and AGREE findings (F-019, F-024, F-025 A-vs-B) carry the
  source-proven facts regardless. **HOLDS.**
- **(e) Self spot-check -- 5 random divergence bullets re-Read at source this session:**
  1. F-013 "site 7 `:952-954` reads STORED `current_principal` `:913` + STORED `interest_rate`
     `:914` + `calculate_remaining_months` `:908-910`": re-Read `amortization_engine.py:908-959`
     -> `remaining = calculate_remaining_months(params.origination_date, params.term_months)`
     `:908-910`; `current_principal = Decimal(str(params.current_principal))` `:913`;
     `rate = Decimal(str(params.interest_rate))` `:914`; `if is_arm and remaining > 0:
     monthly_payment = calculate_monthly_payment(current_principal, rate, remaining)` `:950-954`.
     **Confirmed.**
  2. F-014 "only writer of stored `current_principal` is `update_params` setattr `loan.py:672-674`":
     re-Read `loan.py:668-674` -> `_PARAM_FIELDS = {"current_principal", ...}` `:669`;
     `for field, value in data.items(): if field in _PARAM_FIELDS: setattr(params, field,
     value)` `:672-674`. `grep -rn "current_principal\s*=" app/ --include=*.py` -> only model
     col / 2 schema fields / engine-input kwargs. **Confirmed.**
  3. F-017 "sites 9/10 monthly_payment DEAD; split uses `running_principal*monthly_rate`":
     re-Read `balance_calculator.py:225-289` -> `monthly_payment` assigned `:225/231`, never
     referenced after `:235`; `interest_portion = (running_principal *
     monthly_rate).quantize(...)` `:274-276`; `principal_portion = total_payment_in -
     interest_portion` `:277`. **Confirmed.**
  4. F-021 "`loan.py:968` `.quantize(Decimal('0.01'))` no ROUND_HALF_UP (banker's, A-01 site)":
     re-Read `loan.py:966-968` -> `committed_interest_saved = (original_interest -
     committed_interest).quantize(Decimal("0.01"))` -- no `rounding=` arg. Matches A-01 24-list
     `09_open_questions.md:48` (`loan.py:968`). **Confirmed.**
  5. F-026 "`arm_first_adjustment_months` consumed by zero calc sites": re-ran
     `grep -rn "arm_first_adjustment_months\|arm_adjustment_interval_months" app/` -> only
     `loan_params.py:60-61` (cols), `loan.py:670` (`_PARAM_FIELDS`),
     `validation.py:1450-1451,1471-1472` (schema). Zero calculation references. **Confirmed.**
  Pass rate: **5/5.**
- **(f) Every UNKNOWN names the blocking Q-NN and the resolving answer.** F-013 ->
  Q-09 (developer states `debt_strategy.py:127` site-16 intent + confirms the 16-site count;
  A-09 proposed). F-014 -> Q-15 (canonical real-principal/dispatcher base). F-016 -> Q-11
  (which principal a page MUST display; A-11 proposed). F-025 -> Q-12 (obligations/DTI
  double-count; A-12 proposed) + Q-15 (F-008 co-displayed base). New question **Q-17**
  (5/5-ARM site-7 re-amortization-of-frozen-principal; governs F-013/F-026) filed in
  `09_open_questions.md`. **HOLDS.**

P3-b complete (loan/debt family, F-013..F-026). Phase 3 is NOT complete -- P3-c, P3-d, P3-watchlist,
and P3-reconcile remain; P3-reconcile is the Phase-3 completion gate. P3-a / P2 / P1 / priors
content unmodified (append-only; only the `Finding IDs used` header was updated). Not committed;
developer reviews between sessions.

---

## P3-c: effective_amount sweep / transfer_amount / transfer_amount_computed / Invariant 5 (F-027..F-031)

Session P3-c, 2026-05-16. Scope: the cross-cutting `effective_amount` bypass sweep (audit-plan
section 3.1 "every direct read of actual_amount or estimated_amount... must be listed"),
`transfer_amount`, `transfer_amount_computed`, and the audit-plan section 3.1 / CLAUDE.md Transfer
Invariant 5 budget.transfers-read classification. Read-only (audit plan section 0). Every per-site
verdict below was produced by Reading the ENTIRE surrounding function at the cited `file:line` in
THIS session, not inferred from the P2 catalog or inventory. No developer-reported symptom is owned
here; F-009 (symptom #1) is the same "which amount is read" family as the entries-load bypass and is
cross-referenced, not re-verdicted. Q-08 and Q-14 are PENDING (A-08 / A-14 proposed, not confirmed)
-> rows gated on them get verdict UNKNOWN with the blocking Q named; no guessed verdict (hard rule
5).

**LIVENESS RULE applied:** every bypass site below was grep-confirmed reachable (has at least one
live consumer / is on a routed code path) before classifying. Zero sites were found in dead code;
the F-017 dead-code class (sites 9/10 `monthly_payment`) does not recur in the
`effective_amount`/transfer surface.

### E1 effective_amount/transfer-row reconciliation (verification a + g)

E1 register (`02_concepts.md:3248-3271`) rows owned by P3-c: `transfer_amount`
(`02_concepts.md:3248`), `effective_amount` (`:3249`) = **2 multi-path rows** + the single-path
internal-verify `transfer_amount_computed` (`:3268`, "prefill == monthly_payment+escrow_per_period /
limit/26"). Findings: `effective_amount` -> **F-027** (consolidated) + **F-028** (escalated Q-08
entry-progress cross-anchor DIVERGE cluster); `transfer_amount` -> **F-029**;
`transfer_amount_computed` -> **F-030** (single-path AGREE one-liner). The audit-plan section 3.1
mandatory "shadow / Invariant 5" comparison -> **F-031** (its own finding, extends P3-a F-012 from
the balance calculator to ALL services/routes, per the P3-c mandate). E1 rows in scope: 2 multi-path

+ 1 single-path; findings produced: F-027..F-031 (5). Reconciled 1:1 + the two mandatory escalations
(F-028 Q-08 cluster, F-031 Invariant-5). Zero E1 rows in this family skipped.

#### Canonical substrate (Read at source this session; cited by F-027/F-028/F-029)

`Transaction.effective_amount` (`app/models/transaction.py:221-245`), Read in full this session --
the strict 4-tier rule, docstring `:231` "single source of truth... for what amount a transaction
contributes to balance projections, grid subtotals, and any other calculation context":

- **Tier 1:** `if self.is_deleted: return Decimal("0")` (`:238-239`).
- **Tier 2:** `if self.status and self.status.excludes_from_balance: return Decimal("0")`
  (`:240-241`).
- **Tier 3:** `return self.actual_amount if self.actual_amount is not None ...` (`:245`) --
  `is not None`, NOT truthiness (comment `:242-244`: `actual_amount=Decimal("0")` is valid).
- **Tier 4:** `... else self.estimated_amount` (`:245`).

`Status.excludes_from_balance = True` for **exactly Credit and Cancelled**, Read at
`app/ref_seeds.py:79-84` (Projected/Paid/Received/Settled all `False`). This pins tier-2: any query
that filters `~status_id.in_([CREDIT, CANCELLED])` reproduces tier-2 exactly.

`Transfer.effective_amount` (`app/models/transfer.py:174-182`), Read at source -- the 2-tier
transfer analogue: tier A
`if self.status and self.status.excludes_from_balance: return Decimal("0")` (`:180-181`); tier B
`return self.amount` (`:182`). No is_deleted tier (the Transfer query layer filters `is_deleted`
explicitly instead).

`TransactionEntry` (`app/models/transaction_entry.py`), Read at source -- a single `amount` column
`Numeric(12,2)` NOT NULL CHECK > 0 (`:73`) plus `is_credit` boolean (`:78`);
**no `effective_amount` property, no `is_deleted`, no `status`/`excludes_from_balance`**. Reading
`entry.amount` directly is therefore NOT a bypass of any `effective_amount` tier -- the 4-tier rule
has no analogue on the sub-transactional `TransactionEntry` row; the entry's `amount` IS the
canonical entry value. This single fact resolves the large "entries-sub-transactional" cluster below
to EQUIVALENT-by-domain.

---

### Finding F-027: effective_amount consistency (consolidated ~43-site bypass sweep)

- Concept: `effective_amount` (audit-plan section 3.1 mandatory: list every direct
  `actual_amount`/`estimated_amount` read across services/routes/templates/JS and decide
  whether the omitted tiers can apply)
- Symptom link: contributes to **#1** via the entries-load mechanism (row S1 below; the
  DIVERGE itself is F-002/F-009, cross-referenced not re-verdicted here)
- Canonical producer: `Transaction.effective_amount@transaction.py:221-245` (4-tier,
  restated in the substrate above), `Transfer.effective_amount@transfer.py:174-182`
  (2-tier). No stored/cached `effective_amount` column exists (grep this session: zero
  `effective_amount` columns on any model; it is a `@property`).
- Paths compared: each of the ~43 bypass sites vs the canonical property. The Phase-3
  question per site (per the audit plan): can the omitted tier(s) (is_deleted ->0;
  excludes_from_balance ->0; the `is not None` vs truthiness distinction) reach this read,
  and if so does the displayed/used number differ from `effective_amount`.

#### Per-site verdict table (one row per P2-d consolidated-bypass-table entry)

Reconciliation: the P2-d consolidated table (`02_concepts.md:2353-2410`) is
**30 entries representing ~43 sites = 25 service + 5 route + ~13 template + 0 JS** (P2-d total line
`:2412`). Rows in my table: **30**, 1:1 with the P2-d entries; multi-line entries (e.g.
`year_end_summary_service.py:1123..2096`) are one row as P2-d listed them, site-expansion noted in
the row. **P2-d bypass sites: ~43 (30 table rows); rows in my table: 30.**
Reconciled. Columns: `#` | `file:line` | layer | the direct read | guarded? (the specific
`file:line` that makes the read provably safe, Read this session) | live? | verdict |
classification/cross-ref.

| # | file:line | layer | direct read | guarded? (cite) | live? | verdict | class / cross-ref |
| --- | --- | --- | --- | --- | --- | --- | --- |
| S1 | `balance_calculator.py:292` (374-378,384-385) | svc | entries `amount` + `estimated_amount` (entry-aware) | structural: entry formula gated `status_id==projected_id` `:365`; returns `txn.effective_amount` for every non-entry shape `:353-354` | LIVE (`grid.py:229`, `dashboard_service.py:689`, `_sum_remaining/_all`) | **EQUIVALENT** (intentional entry-aware) -- but **TAGGED**: this IS the symptom-#1 entries-load mechanism | cross-ref **F-002/F-009** (the DIVERGE is recorded there; not re-verdicted) |
| S2 | `dashboard_service.py:239` | svc | `compute_remaining(txn.estimated_amount,...)` | NOT guarded: `compute_remaining@entry_service.py:405-425` takes `estimated_amount`, never consults `actual_amount`; `_entry_progress_fields@:203-246` is not status-gated (settled entry-tracked txn still anchors on estimated) | LIVE (`dashboard_service.py:199`) | **UNKNOWN** | blocked **Q-08**; escalated to **F-028** |
| S3 | `dashboard_service.py:245` | svc | `total > txn.estimated_amount` (over-budget) | same as S2 (`:245`, no actual base for settled) | LIVE | **UNKNOWN** | blocked **Q-08**; **F-028** |
| S4 | `dashboard_service.py:350` | svc | `Account.current_anchor_balance` (stored col) | n/a -- NOT a `Transaction.actual/estimated` read | LIVE (`dashboard_service.py:84`) | **OUT-OF-SCOPE** (anchor source-of-truth column; Phase 4 owns) | Phase 4 (`04_source_of_truth.md`) |
| S5 | `savings_dashboard_service.py:373` | svc | `proj.current_balance` (loan-projection balance) | A-04 dual policy (resolved developer answer) | LIVE (`/savings`, dashboard) | **CROSS-REF** (already DIVERGE/SOURCE in F-001/F-003/F-014/F-016; not an actual/estimated bypass) | **F-001/F-003/F-014/F-016** |
| S6 | `retirement_dashboard_service.py:405,441-442` | svc | `acct.current_anchor_balance` (stored col) | n/a -- anchor column | LIVE (`retirement_dashboard_service.py:184`) | **OUT-OF-SCOPE** (Phase 4) | Phase 4 |
| S7 | `year_end_summary_service.py:519,527` | svc | `func.sum(TransactionEntry.amount)` + credit `case` | structural: TransactionEntry has no effective_amount tier (`transaction_entry.py:73`); parent query `is_deleted False` `:545`, `status_id.in_(settled)` `:547`, `is_envelope True` `:548` | LIVE (`year_end_summary_service.py:462`) | **EQUIVALENT** (sub-transactional sum; no analogue) | -- |
| S8 | `year_end_summary_service.py:1123,1124,1244,1784,1806,1861,2096` (7 sites) | svc | `account.current_anchor_balance` (stored col) | n/a -- anchor column | LIVE (year-end summary chain) | **OUT-OF-SCOPE** (Phase 4) | Phase 4 |
| S9 | `year_end_summary_service.py:1465-1469` | svc | ARM anchor `LoanParams.current_principal` (stored col) | A-04 (resolved) | LIVE (year-end debt schedules) | **CROSS-REF** (A-04 ARM anchor; recorded in F-014/F-026; not actual/estimated) | **F-014/F-026** |
| S10 | `budget_variance_service.py:390-393` | svc | hand-rolled `actual if status.is_settled and not None else estimated` (tiers 3-4) | **GUARDED**: variance query filters `Transaction.is_deleted.is_(False)` `:253,287` (=tier1) AND `~Transaction.status_id.in_(excluded_status_ids)` `:254,288` where `excluded_status_ids=[CREDIT,CANCELLED]` `:207-209` == exactly the `excludes_from_balance` set (`ref_seeds.py:82-83`) (=tier2). Omitted tiers provably cannot reach `_compute_actual` | LIVE (`analytics.py:239` variance_tab) | **EQUIVALENT** | guard `budget_variance_service.py:253-254` / `:287-288` |
| S11 | `calendar_service.py:483` | svc | `account.current_anchor_balance` (stored col) | n/a -- anchor column | LIVE (`calendar_service.py:350`) | **OUT-OF-SCOPE** (Phase 4) | Phase 4 |
| S12 | `transaction_service.py:153` | svc | WRITE `actual_amount = compute_actual_from_entries(entries)` | guarded: immutable-status check `:130-138` (only mutable/Projected settles); WRITES the canonical column that `effective_amount` tier-3 then reads | LIVE (`transactions.py:596`, `carry_forward_service.py:896`) | **EQUIVALENT** (settle mechanism feeding the canonical column; not a read-bypass) | cross-ref **Q-14** (mark_paid asymmetry, A-14) |
| S13 | `carry_forward_service.py:586,878` | svc | `compute_actual_from_entries` + `estimated_amount` (envelope leftover) | A-02 (resolved: Option F data-layer settle); `estimated_amount` is the envelope BUDGET, leftover settles via `settle_from_entries` `:896` | LIVE (carry-forward) | **EQUIVALENT** per **A-02** | A-02 |
| S14 | `credit_workflow.py:229` | svc | hand-rolled `actual if not None else estimated` (tiers 3-4) | **GUARDED tier 2**: `:192-196` raises ValidationError unless `status_id == projected_id` (Projected, excludes_from_balance=False) before reaching `:229`. tier 1 not re-checked by the lock helper (`:95-101`) BUT the value seeds a NEW payback row's `estimated_amount` (`:241`), not a balance read of `txn` -- outside the effective_amount-balance contract | LIVE (`transactions.py:658`) | **EQUIVALENT** (tier-2 hard-guarded `:192-196`; value is a payback seed, not a balance contribution) | Phase-8 hardening note: is_deleted not re-guarded; a soft-deleted source is UI-unreachable here (grid never renders deleted rows) |
| S15 | `entry_credit_workflow.py:112-114` | svc | `sum(e.amount for e if e.is_credit)` | structural: TransactionEntry sub-transactional, no tiers (`transaction_entry.py:73`) | LIVE (entry credit workflow) | **EQUIVALENT** (sub-transactional) | -- |
| S16 | `entry_service.py:70` | svc | WRITE `actual_amount = compute_actual_from_entries` | guarded `if status_id==done_id and txn.entries` `:69`; writes the canonical column feeding tier-3 | LIVE (`entry_service.py:206,267,313`) | **EQUIVALENT** (settle-write, not bypass) | -- |
| S17 | `entry_service.py:365,367,395,397-399,424,446` (~8 sites) | svc | `entry.amount` direct (entry-sum helpers) | structural: TransactionEntry no effective_amount tier (`transaction_entry.py:73`) | LIVE (`_entry_progress_fields`, grid, companion) | **EQUIVALENT** (sub-transactional) | -- |
| S18 | `investment_projection.py:153,187` | svc | `Decimal(str(t.estimated_amount))` (shadow income) | **GUARDED tier 2 INLINE**: `:150` `if not t.status.excludes_from_balance` (active), `:186` same for ytd. tier 1 = caller contract (shadow income pre-filtered, comment `:143-144`). tier3/4 immaterial for transfer shadows (Invariant 3: shadow `estimated==parent amount`, `actual_amount=None`, `transfer_service.py:392-393`) | LIVE (`investment.py:173,497`; `savings_dashboard_service.py:528`; `year_end_summary_service.py:1103,1653`; `retirement_dashboard_service.py:463`) | **EQUIVALENT** (tier-2 inline-guarded `:150,186`; tier-1 caller-contract; tier3/4 immaterial for shadows) | E1 single-path `ytd_contributions` internal-verify |

> **ERRATUM (2026-06-09, deep-quality-hunt #11 / Batch R) -- the S18 EQUIVALENT verdict above is WITHDRAWN for the post-settlement case.** S18's "tier3/4 immaterial ... `actual_amount=None`, `transfer_service.py:392-393`" basis inspected ONLY the shadow-CREATION path (now `_build_shadow` / `_make_shadow_transaction:316-317`). `transfer_service._apply_actual_amount` (now `:570-571`, routed from `update_transfer`'s `actual_amount` kwarg `:663-666`, reachable via the PATCH / mark-done shadow routes) SETS `actual_amount` on BOTH shadows when a transfer is settled with a manual actual. The `Transfer` parent has NO `actual_amount` column, so a settled actual lives only on the shadows -- making `actual_amount=None` false post-settlement and `effective_amount (= actual) != estimated_amount`. The `estimated_amount` reads were therefore NOT equivalent to the `effective_amount` the growth-engine timeline (`build_contribution_timeline`) applies. Fix: `investment_projection.py` `_average_transfer_contribution` / `_sum_year_contributions` now read `effective_amount` (the `Transaction` SSOT). The dependent "HOLDS" rows that cite S18 (W-253, W-296, W-374) inherit this erratum. Line cites have drifted post-split: the now-`effective_amount` reads are at `:227` (avg) / `:327` (ytd), and the active-status guard is the centralized `status_contributes_to_balance` helper, not the inline `excludes_from_balance` checks. See `docs/audits/pylint-cleanup/deep-quality-hunt.md` Batch R.

| R1 | `routes/companion.py:52` | route | `compute_remaining(txn.estimated_amount, txn.entries)` | NOT guarded (settled entry-tracked anchors on estimated) | LIVE (`companion.py:109,149`) | **UNKNOWN** | blocked **Q-08**; **F-028** |
| R2 | `routes/companion.py:54-55` | route | `float(total / txn.estimated_amount * Decimal("100"))` inline pct | div-guard `if txn.estimated_amount > 0` `:55`; estimated base = Q-08 | LIVE | **UNKNOWN** | blocked **Q-08**; **F-028**; also float-on-money (E-10) + route-arithmetic (E-16); cross-ref `goal_progress` GP2 |
| R3 | `routes/entries.py:105` | route | `compute_remaining(txn.estimated_amount, entries)` `:104-106` | NOT guarded (same Q-08 base) | LIVE (`entries.py:167,197,251,319,367,413`) | **UNKNOWN** | blocked **Q-08**; **F-028** |
| R4 | `routes/dashboard.py:128` | route | WRITE `txn.actual_amount = actual_amount` | n/a (write); the mark_paid/mark_done settle asymmetry | LIVE (route `mark_paid`) | **UNKNOWN** | blocked **Q-14** (A-14 already in `09_open_questions.md`) |
| R5 | `routes/transactions.py:614` | route | WRITE `txn.actual_amount = actual_amount` | n/a (write); Q-14 counterpart | LIVE (route `mark_done`) | **UNKNOWN** | blocked **Q-14** (A-14) |
| T1 | `grid/_transaction_cell.html:17` | tmpl | 2-tier mirror `t.actual_amount if ... is not none else t.estimated_amount` | display face-amount + status badge `:33` (`-- {{ t.status.name }}`); grid query `is_deleted=False` (`grid.py:222`) so deleted never rendered (tier1 unreachable); Credit/Cancelled intentionally shown as face amount + badge (display concept, not balance) | LIVE (`grid.html`, transactions/transfers cell endpoints) | **EQUIVALENT-for-display** (face amount + badge, not a balance contribution; omitted tiers unreachable or intentionally not applied to a display) | Phase-6 DRY (hand-rolled 2-tier mirror) |
| T2 | `grid/_transaction_cell.html:21` | tmpl | `remaining = t.estimated_amount - es.total` (Jinja arithmetic) | guarded `show_progress = es is not none and t.status_id == STATUS_PROJECTED` `:19` (Projected-only -> for Projected `estimated==effective`, so numerically EQUIVALENT) | LIVE | **EQUIVALENT-numeric** + **E-16 coding-standards VIOLATION** (Jinja money arithmetic) | E-16 (recorded; same class as F-019 `_escrow_list.html:37`); cross-ref F-002 Pair C |
| T3 | `grid/_transaction_cell.html:33,43,48` | tmpl | aria-label / progress `estimated_amount` reads | display-only string formatting | LIVE | **EQUIVALENT** (display-only) | -- |
| T4 | `grid/_mobile_grid.html:92,179` | tmpl | 2-tier mirror x2 | same as T1: status badge `:103,190`; Projected-gated progress `:94,181`; grid query `is_deleted=False` | LIVE (`grid.html` mobile) | **EQUIVALENT-for-display** | Phase-6 DRY (mirror duplicated x2 within file) |
| T5 | `grid/_mobile_grid.html:96,183` | tmpl | `remaining = txn.estimated_amount - es.total` (Jinja x2) | guarded `show_progress ... STATUS_PROJECTED` `:94,181` | LIVE | **EQUIVALENT-numeric** + **E-16 VIOLATION** | E-16 |
| T6 | `grid/_mobile_grid.html:103,190` | tmpl | aria-label `estimated_amount` reads | display-only | LIVE | **EQUIVALENT** (display-only) | -- |
| T7 | `grid/_carry_forward_preview_modal.html:139,143,147` | tmpl | `plan.transaction.estimated_amount` preview labels | deliberately the "estimate" label (text literally renders "estimate $X" / "Defer ... ($X)"); A-02 governs carry-forward semantics | LIVE (`transactions.py` carry-forward-preview endpoint) | **EQUIVALENT-for-display** (intentional estimate label, not a hand-rolled effective_amount mirror) | A-02 |

JS: **0 sites.** `grep -rn "actual_amount\|estimated_amount\|effective" app/static/js/` re-run this
session returns only `effective_month` / `effective_year` / `inflation_effective_month` (form-field
names on `app.js:214,217,312`, unrelated to the monetary `effective_amount`). Consistent with E-17
and P2-d.

#### Verdict (consolidated -- counts by class over the 30 rows / ~43 sites)

- **EQUIVALENT: 17 rows** (S1, S7, S10, S12, S13, S14, S15, S16, S17, S18; T1, T2, T3, T4,
  T5, T6, T7). Each names its guard `file:line` above. S1 is additionally **TAGGED** as the
  symptom-#1 entries-load mechanism (the DIVERGE is F-002/F-009, not re-verdicted here).
- **UNKNOWN, blocked Q-08: 5 rows** (S2, S3, R1, R2, R3) -- escalated to **F-028**.
- **UNKNOWN, blocked Q-14: 2 rows** (R4, R5) -- A-14 proposed, already in
  `09_open_questions.md`.
- **OUT-OF-SCOPE (anchor stored-column source-of-truth; Phase 4 owns): 4 rows** (S4, S6,
  S8, S11). These read `Account.current_anchor_balance`, NOT `Transaction.actual/estimated`
  -- not `effective_amount` bypasses; listed per the audit-plan "list every direct read"
  mandate and routed to `04_source_of_truth.md`.
- **CROSS-REF (A-04 loan-projection / ARM-anchor; already DIVERGE in the loan family): 2
  rows** (S5, S9) -> F-001/F-003/F-014/F-016/F-026.
- **DEAD_CODE: 0.** Every site grep-confirmed reachable (LIVENESS RULE).
- **New standalone DIVERGE: 0.** No site computes a checking-account balance from raw
  transaction data bypassing the property (the true Invariant violation per
  `01_inventory.md:1618-1620`); the only live numeric drift on this surface is (i) the
  entries-load axis already escalated as F-002/F-009, and (ii) the Q-08 entry-progress
  cross-anchor inconsistency escalated as F-028 below.
- Overall: **DIVERGE** label for the concept (because S1 feeds the F-002/F-009 SILENT_DRIFT
  and F-028 carries a SILENT cross-anchor inconsistency that holds regardless of Q-08), with
  the sweep's structural conclusion that **no NEW silent balance-bypass exists**: every
  hand-rolled mirror (S10, S14, T1, T4) is tier-1/2-guarded by an upstream query filter or a
  status precondition Read at source, and every entry-`amount` read is sub-transactional
  with no `effective_amount` analogue.
- If DIVERGE: classification: SILENT_DRIFT (S1 entries-load -> F-002/F-009; F-028
  cross-anchor), with Phase-6 DRY notes (the 4 hand-rolled 2-tier mirrors S10/S14/T1/T4
  reproduce tiers 3-4 inline -- a drift substrate if the property's tier-3/4 rule changes)
  and E-16 coding-standards rows (T2, T5; same class as F-019).
- Open questions for the developer: **Q-08** (`09_open_questions.md:288-323`; entry-progress
  estimated-vs-actual base for a settled txn -- governs S2/S3/R1/R2/R3, escalated F-028),
  **Q-14** (`09_open_questions.md:561-613`; mark_paid vs mark_done settle asymmetry -- R4/R5,
  A-14 proposed). No NEW question raised by the sweep (every other site is EQUIVALENT with a
  source-cited guard, or Phase-4/A-04/A-02-governed).

---

### Finding F-028: entry-progress base + cross-anchor inconsistency (escalated Q-08 cluster)

- Concept: `effective_amount` (entry-progress sub-cluster: S2/S3/R1/R2/R3 of F-027) /
  `goal_progress` GP2 / `entry_remaining`
- Symptom link: none developer-reported; same "which amount is read" family as #1
- Paths compared:
  - A = dashboard bill row: `_entry_progress_fields@dashboard_service.py:203-246`
    (`entry_remaining` `:239`, `entry_over_budget` `:245`) vs the SAME bill dict's
    `bill["amount"]` set elsewhere in the bill builder.
  - B = companion entry data: `_build_entry_data@companion.py:47-64`
    (`remaining` `:52`, `pct` `:54-56`).
  - C = entries partial: `_render_entry_list@entries.py:101-113` (`remaining` `:104-106`).
  - Pairs A-B, A-C, B-C (all three compute "entry remaining / progress" for an
    entry-tracked txn).
- Path A: `app/services/dashboard_service.py:203-246` Read at source --
  `is_tracked = txn.template is not None and txn.template.is_envelope` `:224-227`; if tracked and
  `txn.entries`, `remaining = compute_remaining(txn.estimated_amount, txn.entries)` `:239`,
  `entry_over_budget = total > txn.estimated_amount` `:245`. **Not status-gated** -- a
  DONE/Received/Settled entry-tracked txn with `actual_amount` populated still anchors
  `remaining`/`over_budget` on `estimated_amount`.
- Path B: `app/routes/companion.py:47-64` Read at source --
  `remaining = compute_remaining(txn.estimated_amount, txn.entries)` `:52`;
  `pct = float(total / txn.estimated_amount * Decimal("100")) if txn.estimated_amount > 0 else 0.0`
  `:54-56`.
- Path C: `app/routes/entries.py:101-113` Read at source --
  `remaining = entry_service.compute_remaining(txn.estimated_amount, entries)` `:104-106`.
- `compute_remaining@entry_service.py:405-425` Read at source: pure
  `estimated_amount - sum(e.amount for e in entries)`; it receives `estimated_amount` as a parameter
  and **cannot** switch on status or consult `actual_amount` (no `txn` argument).
- Compared dimensions:
  - Effective-amount logic: **DIVERGES from the property by design at every site** -- all three
    anchor on raw `estimated_amount`, never tier-3 `actual_amount`, even for a settled txn whose
    `actual_amount` is populated (the entry-derived actual written by
    `settle_from_entries@transaction_service.py:153`). A/B/C agree WITH EACH OTHER (identical
    `compute_remaining` formula, same estimated base).
  - **Cross-anchor inconsistency inside one bill row (holds regardless of Q-08):** in Path A the
    bill row's `entry_remaining`/`entry_over_budget` anchor on `estimated_amount` (`:239,245`) while
    the same bill dict's displayed `amount` is `txn.effective_amount` (the dashboard bill builder
    uses `effective_amount` for the amount cell -- P2-a / F-002 substrate,
    `dashboard_service.py:191`-class read). For a DONE entry-tracked txn with
    `actual_amount = $100`, `estimated_amount = $120`, entries summing `$80`: the amount cell shows
    `$100` (effective=actual) while "remaining" shows `$120 - $80 = $40` and over-budget is `False`
    -- two anchors (actual vs estimated) for one txn on one row, no error.
  - Status filter / scenario / is_deleted / period: not the divergence axis (the bill list is the
    dashboard's current-period set; A/B/C share it).
  - Quantization: `compute_remaining` returns a raw `Decimal` subtraction feeding
    `Numeric(12,2)`-precision display; not the axis. `companion.py:54` casts to `float` (E-10
    concern) -- recorded, not the numeric driver.
- Divergences:
  - A/B/C vs `Transaction.effective_amount`: estimated-only base, ignores tier-3 `actual_amount` for
    a settled entry-tracked txn. SILENT (no error; the number is just the planned-budget remaining,
    not the actual-outcome remaining).
  - Path A internal cross-anchor: `amount` (effective=actual when settled) vs `entry_remaining`
    (estimated) on one bill row. SILENT.
- Risk -- worked example (the A-08-proposed shape, `09_open_questions.md:305-323`): entry-tracked
  DONE txn, `estimated_amount = $120.00`, `actual_amount = $100.00` (written by
  `settle_from_entries` = sum of entries), three cleared entries summing `$80.00` plus one `$20.00`
  entry. Bill row: amount cell `= effective_amount = actual = $100.00`;
  `entry_remaining = compute_remaining($120.00, entries) = 120.00 - 100.00 = $20.00`;
  `entry_over_budget = (100.00 > 120.00) = False`. If interpretation (2) "budget = what you spent"
  is intended, remaining should anchor on `actual_amount` and the row is internally inconsistent
  (amount=$100 actual, remaining computed off $120 estimate). If interpretation (1) "budget = what
  you allocated" is intended, the remaining is correct but the amount cell
  ($100 actual) and the remaining ($120-based) still rest on two different anchors the user reads as
  one mental model.
- Verdict: **UNKNOWN** for the estimated-vs-actual base -- blocked on **Q-08**
  (`09_open_questions.md:288-323`; A-08 proposed, pending: interp (1) "what you allocated" =
  current code AGREE, interp (2) "what you spent" = DIVERGE + `compute_remaining` must take
  the txn and switch on `is_settled`). The **cross-anchor inconsistency** (amount uses
  `effective_amount`, remaining uses `estimated_amount` on one row) is a recorded **SILENT
  DIVERGE independent of Q-08** -- the A-08-proposed text itself states it "is a separate
  concern worth labeling regardless of which interpretation is chosen".
- If DIVERGE (estimated/actual axis conditional on Q-08): SILENT_DRIFT. The cross-anchor
  inconsistency: SILENT_DRIFT unconditionally.
- Open questions for the developer: **Q-08** (governing the estimated/actual axis; A-08
  proposed). No new question (Q-08 already frames both interpretations and the cross-anchor
  concern). Cross-link `goal_progress` GP2 (`02_concepts.md:3250`), `entry_remaining`
  (`02_concepts.md:3252`, Gate-A BLOCKED Q-08), F-002 Pair C, F-027 rows S2/S3/R1/R2/R3.

---

### Finding F-029: transfer_amount consistency

- Concept: `transfer_amount`
- Symptom link: none directly (Invariant-3/4 integrity; year-end double-count cross-check)
- Paths compared (every producer pair, P2-d `:2242-2251`):
  - A = stored `Transfer.amount` (`transfer.py:142`) rendered via
    `transfers/_transfer_cell.html` / `transfers/list.html`.
  - B = `Transfer.effective_amount@transfer.py:174-182` (canonical read, 2-tier).
  - C = shadow pair `Transaction.effective_amount` (the two shadows the balance calculator
    actually sums; Invariant 3).
  - D = `_compute_transfers_summary@year_end_summary_service.py:636-683` year-end
    per-destination display total (`total_amount += t.amount` `:679`).
  - Pairs A-B, A-C, A-D, B-C, B-D, C-D.
- Path A/B (Read at source): `Transfer.amount@transfer.py:142` is `Numeric(12,2)` NOT NULL;
  `Transfer.effective_amount@:174-182` = (`:180-181`) `0` if `status.excludes_from_balance` else
  (`:182`) `amount`. Mutated EXCLUSIVELY by
  `transfer_service.create_transfer/update_transfer/restore_transfer` (Invariant 4) -- grep `F-031`
  confirms zero unauthorized writers.
- Path C (Read at source): `transfer_service.create_transfer@transfer_service.py:361-412` constructs
  `Transfer(amount=amount)` `:370` and BOTH shadows with `estimated_amount=amount` (`:392` expense,
  `:412`-region income), `actual_amount=None` `:393`, identical `status_id` `:388,409`, identical
  `pay_period_id` `:386,407`. Transfer has **no `actual_amount` column** (comments `:457,567`). So
  each shadow's `Transaction.effective_amount` = tier-4 `estimated_amount` = `Transfer.amount` (for
  non-excluded status); for Cancelled it is tier-2 `0`, exactly matching `Transfer.effective_amount`
  tier-A `0`. Invariant 3 holds at the producer across the status lifecycle (no path sets a shadow
  `actual_amount`; Invariant 4 forbids direct shadow mutation -- F-031).
- Path D (Read at source): `_compute_transfers_summary@:636-683` queries `Transfer` (`:658`)
  filtered `user_id` `:661`, `scenario_id` `:662`, `pay_period_id.in_(period_ids)` `:663`,
  `is_deleted.is_(False)` `:664`, `~status_id.in_(excluded_ids)` `:665` (Credit/Cancelled). Because
  `:665` pre-excludes the `excludes_from_balance` statuses, the raw `t.amount` summed at `:679`
  equals `t.effective_amount` for every surviving row -- the `effective_amount` "bypass" is
  contract-safe (the query filter reproduces tier-A).
- Compared dimensions:
  - Source-of-truth column: A reads stored `Transfer.amount`; B is the 2-tier read of the same
    column; C is the shadow `Transaction.effective_amount` (estimated=amount by Invariant 3); D sums
    `Transfer.amount` over a status/period/scenario-filtered set. All four trace to the SAME stored
    `Transfer.amount`, mutated only by the authorized mutator.
  - Status filter: A/B per-row (B zeroes Cancelled); C per-shadow (`Transaction.effective_amount`
    zeroes Credit/Cancelled); D excludes Credit/Cancelled at the query `:665`. Consistent: a
    Cancelled transfer contributes 0 on B, 0 on each shadow (C), and is filtered out of D.
  - **ORM load-context (mandatory P3-b dimension): D is self-contained** --
    `_compute_transfers_summary` issues its own `Transfer` query (`:658`, no caller-supplied list);
    A/B render a `Transfer` the route already loaded; C is whatever shadow `Transaction` rows the
    balance path loaded. No load-context-dependent value divergence in `transfer_amount` itself (the
    Transfer query options do not change `amount`).
  - is_deleted: D filters `:664`; the balance calculator (C) filters shadow `Transaction.is_deleted`
    at its query (F-012). A/B render a specific transfer (route-scoped). Consistent.
  - Quantization: `Transfer.amount` is `Numeric(12,2)`; D sums pre-quantized values, no re-quantize.
    AGREE.
  - Double-count axis (P2-d Multi-path flag / Gate F4): D's result is consumed ONLY at
    `year_end_summary_service.py:223` as the standalone `"transfers_summary"` key in the
    year-end dict (`:79` lists it as a sibling section; empty fallback `:2243`). It is
    **never summed into** `net_worth` (`_compute_net_worth@:689`, separate function;
    `_sum_net_worth_at_period@:2181`) or `account_balance`/`debt_total`. net_worth /
    account_balance consume shadow `Transaction` rows via `calculate_balances` (Invariant 5,
    F-012/F-031). The Transfer-based display total and the shadow-based balances are never
    added into one figure -> **no double-count**. Per Invariant 3, `t.amount` already equals
    the sum of its two shadow `effective_amount` values, so even per-transfer the two
    representations reconcile; the only double-count risk (one total summing BOTH a Transfer
    AND its shadows) occurs in **no code path**.
- Divergences: none. A/B/C/D all derive from the single stored `Transfer.amount` mutated
  only by the authorized mutator; the year-end display total is a separate widget never
  summed against the shadow-based balances.
- Risk: none for `transfer_amount` consistency -- Invariant 3 (shadow == parent, Read at
  `transfer_service.py:370,392-393`) and Invariant 4 (sole-mutator, F-031) make A=B=C and D
  a filtered display of the same column; no consumer adds D to the shadow-based net_worth.
- Verdict: **AGREE.**
- If DIVERGE: n/a.
- Open questions for the developer: none. Cross-link **Q-12** (`09_open_questions.md:459-512`)
  -- the obligations/DTI mortgage double-count is a DIFFERENT, still-open concern (A-12
  proposed, pending) and is explicitly NOT this transfers-summary-vs-net_worth question;
  recorded here so P3-reconcile does not conflate them. Cross-link **F-012** (Invariant 5,
  balance calculator), **F-031** (Invariant 5 extended), **F-025** (`dti_ratio` Q-12).
  PA-25/PA-08 (`02_concepts.md:2270-2275`) are Phase-7/8 (recurrence-producer boundary
  tests; `carry_forward_unpaid` scenario filter) -- not Phase-3 consistency.

---

### Finding F-030: transfer_amount_computed consistency (single-path internal verify)

- Concept: `transfer_amount_computed` (E1 single-path scoped internal-verify,
  `02_concepts.md:3268`)
- Symptom link: none directly
- Paths: single route-resident derivation BY DESIGN (no service producer; the only "consumers
  but no producers" token, `02_concepts.md:2541-2543`). Scoped internal check: the loan
  pre-fill (`loan.create_payment_transfer@routes/loan.py:1213-1241`) must equal the loan
  dashboard's displayed `monthly_payment` + `escrow_per_period`; the investment pre-fill
  (`investment.create_contribution_transfer@routes/investment.py:668-670`) must equal
  `calculate_investment_inputs`' `annual_contribution_limit / 26` with the `$500` fallback.
- Internal-consistency check: site 13 of the F-013 16-site table is exactly
  `routes/loan.py:1102-1104` (refinance, by design) and site 14 `routes/loan.py:1225-1229`
  is the `create_payment_transfer` ARM P&I -- the pre-fill consumes the SAME
  `calculate_monthly_payment` triple as the dashboard (P3-b F-013 substrate). It is therefore
  consistent-by-construction with `monthly_payment`/`escrow_per_period` AND inherits F-013's
  ARM input-divergence (cross-link, not re-verdicted). The investment pre-fill's `limit/26`
  reads the same `annual_contribution_limit` as `contribution_limit_remaining`
  (`02_concepts.md:3262-3263`).
- Verdict: **AGREE** (single-path, route-resident by design; pre-fill reads the same
  producers as `monthly_payment`/`escrow_per_period`/`annual_contribution_limit`; no
  recompute drift). Inherits the F-013 ARM `monthly_payment` input risk for the loan pre-fill
  (cross-link only).
- Open questions for the developer: none. Cross-link **F-013** (the loan pre-fill rides the
  16-site `monthly_payment` substrate), **Q-12** (`02_concepts.md:2543-2545`: route-resident
  derivation as a Phase-6 SRP example; A-12 proposed). Phase-6 SRP note recorded for
  `06_dry_solid.md` (route-layer financial derivation), not actioned here.

---

### Finding F-031: shadow / budget.transfers reads -- Transfer Invariant 5 (all services + routes)

- Concept: cross-cutting balance integrity (audit-plan section 3.1 mandatory: "list every
  read of the transfers table and classify it as legitimate (CRUD, recurrence template
  management) or as a violation"; CLAUDE.md Transfer Invariant 5 / E-09, `00_priors.md:119,
  202-204`)
- Symptom link: none (extends P3-a **F-012** -- which proved the balance calculator clean --
  to ALL services and routes, per the P3-c mandate)
- Invariant 5 verbatim (`00_priors.md:119`): "Balance calculator queries ONLY
  budget.transactions. NEVER also query budget.transfers."
- Evidence: `grep -rn "budget\.transfers\|\.transfers\b\|Transfer\b" app/services app/routes`
  re-run THIS session (2026-05-16); every match triaged by Reading the cited site. Comment /
  docstring / string-literal / display-name-slicing matches are NOT reads of the `Transfer`
  table and are excluded from the classified table (enumerated under "Non-reads" below).

#### Classified table -- every actual `Transfer`-model read

| Site (function / context) | What it does | Classification |
| --- | --- | --- |
| `balance_calculator.py` -- docstring `:17,:19` ONLY; **zero** `query(Transfer)` / `from app.models.transfer` / `.transfers` | balance engine; docstring states it "does NOT query or process Transfer objects directly" | **INVARIANT 5 HOLDS** (grep-confirmed zero Transfer read in the module; = P3-a F-012, re-verified this session) |
| `loan_payment_service.py:18` -- docstring ONLY ("It NEVER queries budget.transfers"); zero query | loan payment / balance path | **HOLDS (extends F-012)** -- the loan balance path is also clean; grep confirms zero `Transfer` query in the file |
| `grid.py:54-63,:237` -- comment + shadow display-name prefix slicing; `:237` "no separate Transfer query needed"; zero query | grid route; reads shadow `Transaction` rows only | **LEGITIMATE** (no Transfer read; balance via shadow `Transaction`) |
| `transfer_service.py:36,195,231,361,...` | THE authorized mutator -- `db.session.get/Transfer(...)` for create/update/delete/restore | **LEGITIMATE** (Invariant 4 sole mutator) |
| `transfer_recurrence.py:21,172,257,309` | recurrence template generate (`:172`) / resolve (`:257`) / existing-entries map (`:309`); writes delegated to `transfer_service` | **LEGITIMATE** (recurrence template management) |
| `carry_forward_service.py:23` (doc); moves via `transfer_service.update_transfer` (A-07) | discrete/transfer-branch carry-forward; no direct balance Transfer read | **LEGITIMATE** (CRUD via authorized mutator) |
| `routes/transfers.py:23,491,546,636,668,985,1170` | transfer cell render / template-entry queries / dedup (`:985-994`) / get (`:1170`) for CRUD + status actions | **LEGITIMATE** (CRUD; not balance computation) |
| `routes/transactions.py:20,282` | `db.session.get(Transfer, txn.transfer_id)` -- resolve parent transfer for a shadow cell render (transfer-edit-form detection) | **LEGITIMATE** (CRUD / display) |
| `routes/accounts.py:689,691-694` | account deletion: query remaining `Transfer` rows referencing the account, delete via `transfer_service.delete_transfer` | **LEGITIMATE** (CRUD / cleanup via authorized mutator) |
| `year_end_summary_service.py:42,658,679` (`_compute_transfers_summary`) | year-end transfers-by-destination DISPLAY total: `query(Transfer)` `:658` filtered `is_deleted False` `:664` + `~[CREDIT,CANCELLED]` `:665`; `total_amount += t.amount` `:679` | **LEGITIMATE display aggregation** -- NOT the balance calculator, NOT summed into net_worth/account_balance (double-count RESOLVED -> F-029, AGREE) |

`app/utils/archive_helpers.py:57` (archival/retention sweep) was listed by P2-d
(`02_concepts.md:2295`) and is outside this grep's `app/services app/routes` scope; recorded here
for completeness as **LEGITIMATE** (archival, not balance computation).

Non-reads excluded from the table (triaged this session, not `Transfer`-table reads):
`loan_payment_service.py:18`, `carry_forward_service.py:23`, `balance_calculator.py:17,19`,
`loan.py:1178` (docstrings); `_recurrence_common.py:67,102` ("(Transaction|Transfer)Template"
/ SOC label string); `auth_service.py:274` (category seed string `"Savings Transfer"`);
`investment_projection.py:142,215,263,282`, `spending_trend_service.py:239`,
`budget_variance_service.py:194`, `entry_service.py:157`, `recurrence_engine.py:403`,
`dashboard.py:93`, `state_machine.py:2,6,49`, `transactions.py:387,539,653,687,730,1049` (comments /
guards reading shadow `Transaction`, not `Transfer`).

- Compared dimension: source-of-truth read. The invariant axis is "does a balance/aggregate
  computation query `budget.transfers`". Every classified row is CRUD, recurrence-template
  management, the authorized mutator, archival, or the standalone year-end display
  aggregation. The balance calculator and the loan balance path both grep-clean.
- Divergences: **none**. **VIOLATION: none** -- no balance/aggregate computation path queries
  `budget.transfers`. Invariant 5 / E-09 satisfied across all services and routes. The single
  money aggregation over `budget.transfers` (`_compute_transfers_summary@:679`) is a
  standalone year-end display widget, resolved AGREE (no double-count) in F-029.
- Risk: none -- the double-count the invariant guards against (a transfer counted once as a
  `Transfer` row and again as its two shadow `Transaction` rows in one balance total) occurs
  in no code path: balance totals consume only shadow `Transaction` rows; the only
  `Transfer`-based money sum is a separate informational table.
- Verdict: **AGREE** (Invariant 5 HOLDS for all services and routes; F-012 extended; zero
  VIOLATION).
- If DIVERGE: n/a. (A genuine VIOLATION here would have been a CRITICAL-candidate; none
  found.)
- Open questions for the developer: none. Cross-link **F-012** (balance-calculator
  Invariant 5, P3-a), **F-029** (`transfer_amount` / `_compute_transfers_summary`
  double-count AGREE), **Q-12** (the separate obligations/DTI mortgage double-count,
  A-12 proposed -- NOT this finding).

---

### P3-c verification (a-g)

- **(a) Per-site table has a row for EVERY P2-d bypass entry.** P2-d consolidated table
  (`02_concepts.md:2353-2410`) = 30 entries representing ~43 sites (25 svc + 5 route + ~13
  tmpl + 0 JS, P2-d total `:2412`). My table = **30 rows** (S1-S18, R1-R5, T1-T7), 1:1 with
  the P2-d entries (multi-line entries kept as one row, site-expansion noted, e.g. S8 = 7
  sites, S17 ~ 8 sites). **P2-d bypass entries: 30 (~43 sites); rows in my table: 30.**
  Reconciled, zero missing. **HOLDS.**
- **(b) Every DIVERGE has a worked example.** The only escalated DIVERGE is F-028 (Q-08
  cross-anchor): worked example `estimated $120 / actual $100 / entries $80 -> amount cell
  $100 vs entry_remaining $40 (or $20 in the second worked case), over_budget False` -- a
  concrete input where the direct `estimated_amount` read yields a different anchor than
  `effective_amount`. S1's DIVERGE is F-002/F-009 (their worked `$962.34`/`$45.71`/`$160`
  examples, cross-referenced not duplicated). S5/S9 DIVERGEs are F-014/F-016 (their
  `$312,000`/`$297,450` worked examples). **HOLDS.**
- **(c) Every EQUIVALENT names the specific guard `file:line`.** S10 -> query filters
  `budget_variance_service.py:253-254/287-288` (=tiers 1+2, with `excluded_status_ids`
  `:207-209` == `excludes_from_balance` set proven at `ref_seeds.py:82-83`). S14 ->
  `credit_workflow.py:192-196` (status==Projected hard precondition). S18 ->
  `investment_projection.py:150,186` (inline `not status.excludes_from_balance`). S7/S15/S17
  -> structural `transaction_entry.py:73` (no effective_amount tier on TransactionEntry).
  S12/S16 -> write-the-canonical-column under status guard `transaction_service.py:130-138`
  / `entry_service.py:69`. T1/T4 -> `grid.py:222` (is_deleted query filter, deleted never
  rendered) + status-badge display intent `:33/:103,190`. T2/T5 -> `STATUS_PROJECTED` gate
  `_transaction_cell.html:19` / `_mobile_grid.html:94,181`. T7 -> A-02. No "looks fine"
  verdict. **HOLDS.**
- **(d) Invariant-5 grep re-run and cited; every transfers read classified.**
  `grep -rn "budget\.transfers\|\.transfers\b\|Transfer\b" app/services app/routes` re-run
  2026-05-16 (output captured this session); F-031 classifies every `Transfer`-model read
  (10 LEGITIMATE/HOLDS rows + the non-read exclusion list). Zero VIOLATION. **HOLDS.**
- **(e) Self spot-check -- 5 random rows re-Read at source this session (mix EQUIVALENT /
  UNKNOWN):**
  1. **S10 EQUIVALENT** "variance query filters is_deleted + ~[CREDIT,CANCELLED]": re-Read
     `budget_variance_service.py:207-209,243-254` -> `excluded_status_ids = [status_id(CREDIT),
     status_id(CANCELLED)]` `:207-209`; query `.filter(... Transaction.is_deleted.is_(False)`:253`, ~Transaction.status_id.in_(excluded_status_ids)`:254`)`. **Confirmed** (tiers
     1+2 pre-filtered; `_compute_actual:381-393` never sees a deleted/excluded row).
  2. **S14 EQUIVALENT** "mark_as_credit forces Projected before :229": re-Read
     `credit_workflow.py:192-196,229` -> `if txn.status_id != projected_id: raise
     ValidationError(...)` `:192-196`; `payback_amount = txn.actual_amount if ... is not None
     else txn.estimated_amount` `:229`. **Confirmed** (tier-2 hard-guarded; value seeds a new
     payback row `:241`).
  3. **R1 UNKNOWN Q-08** "companion `_build_entry_data` uses estimated base": re-Read
     `companion.py:52,54-55` -> `remaining = compute_remaining(txn.estimated_amount,
     txn.entries)` `:52`; `pct = float(total / txn.estimated_amount * Decimal("100")) if
     txn.estimated_amount > 0 else 0.0` `:54-56`. **Confirmed** (estimated-only, Q-08).
  4. **S18 EQUIVALENT** "investment_projection inline excludes_from_balance guard": re-Read
     `investment_projection.py:147-153,184-187` -> `active_contributions = [t for t in
     all_contributions if not t.status.excludes_from_balance]` `:148-151`; ytd loop `if ...
     and not t.status.excludes_from_balance: ytd += Decimal(str(t.estimated_amount))`
     `:184-187`. **Confirmed** (tier-2 inline-guarded).
  5. **T1 EQUIVALENT-for-display** "`_transaction_cell.html:17` 2-tier mirror, deleted never
     rendered + status badge": re-Read `_transaction_cell.html:17,19,33` -> `display_amount
     = t.actual_amount if t.actual_amount is not none else t.estimated_amount` `:17`;
     `show_progress = es is not none and t.status_id == STATUS_PROJECTED` `:19`; aria/title
     append `-- {{ t.status.name }}` `:33`. **Confirmed** (face-amount + status badge
     display; grid query `grid.py:222` `is_deleted=False`). Pass rate: **5/5.**
- **(f) Every UNKNOWN names the blocking Q-NN.** F-027 rows S2,S3,R1,R2,R3 -> **Q-08**
  (`09_open_questions.md:288-323`, A-08 proposed); rows R4,R5 -> **Q-14**
  (`09_open_questions.md:561-613`, A-14 proposed). F-028 -> **Q-08** (estimated/actual axis;
  the cross-anchor sub-divergence is recorded SILENT *independent* of Q-08). No UNKNOWN
  lacks a governing filed Q. **HOLDS.**
- **(g) Every E1 effective_amount/transfer row maps to a finding.** E1 `effective_amount`
  (`02_concepts.md:3249`) -> F-027 (+F-028 escalation); E1 `transfer_amount` (`:3248`) ->
  F-029; E1 single-path `transfer_amount_computed` (`:3268`) -> F-030; audit-plan 3.1
  shadow/Invariant-5 -> F-031 (extends F-012). 2 multi-path E1 rows + 1 single-path =
  F-027..F-031 (5 findings). Reconciled; zero E1 row in this family skipped. **HOLDS.**

P3-c complete (effective_amount sweep / transfer_amount / transfer_amount_computed / Invariant 5,
F-027..F-031). Phase 3 is NOT complete -- P3-d, P3-watchlist, and P3-reconcile remain; P3-reconcile
is the Phase-3 completion gate. P3-a / P3-b / P2 / P1 / priors content unmodified (append-only; only
the `Finding IDs used` header was updated). No source, test, or migration file modified this
session. Not committed; developer reviews between sessions.

---

## P3-d1: income / tax family (F-032..F-040)

Session P3-d1 (part 4a of Phase 3), 2026-05-16. Scope: the income/tax family only --
`paycheck_gross`, `paycheck_net`, `taxable_income`, `federal_tax`, `state_tax`, `fica`,
`pre_tax_deduction`, `post_tax_deduction`. Read-only (audit plan section 0). Every verdict below was
produced by Reading the ENTIRE producing function at the cited `file:line` in THIS session
(`paycheck_calculator.py` whole module 1-505; `tax_calculator.py` whole module 1-322;
`calibration_service.py` whole module 1-146; `savings_dashboard_service.py:155-194,201-292`;
`recurrence_engine.py:740-767`), not inferred from the P2 catalog or inventory. No
developer-reported symptom is owned here (symptoms #1-#5 are balance/loan); the family is one
canonical engine (`paycheck_calculator.calculate_paycheck` orchestrating `tax_calculator` /
`calibration_service`) consumed with divergent inputs, so most rows are expected AGREE. The three
governed findings P2-c / Gate F located (FICA SS-cap bypass, legacy `calculate_federal_tax`
dead-code, off-engine `paycheck_gross` dti base) are full-schema. Q-13 is PENDING (A-13 proposed,
`09_open_questions.md:514-559`) -> the Q-13-gated calibrate_preview sub-pairs get verdict UNKNOWN
with Q-13 named; no guessed verdict (hard rule 5). LIVENESS rule applied to the legacy wrapper
(governed #2).

### E1 income/tax-row reconciliation (verification a)

E1 register (`02_concepts.md:3237-3244`) income/tax rows = **8 multi-path**: `paycheck_gross`
(`:3237`), `paycheck_net` (`:3238`), `taxable_income` (`:3239`), `federal_tax` (`:3240`),
`state_tax` (`:3241`, P2-c override of 1.7.4 single-path), `fica` (`:3242`), `pre_tax_deduction`
(`:3243`), `post_tax_deduction` (`:3244`). Zero income/tax tokens in the E1 single-path
internal-verify list (`02_concepts.md:3259-3271` is loan/retirement/savings/ transfer only --
`loan_remaining_months`, `contribution_limit_remaining`, `ytd_contributions`,
`emergency_fund_coverage_months`, `cash_runway_days`, `transfer_amount_computed`,
`pension_benefit_*`; none income/tax). Findings F-032..F-039 map the 8 rows **1:1**; F-040 is the
standalone governed legacy-`calculate_federal_tax` dead-code finding (analogous to P3-a's standalone
F-009 and P3-b's standalone F-026 -- it has no single E1 row of its own; it is the governed
dead-code item flagged under both `taxable_income` and `federal_tax`).

| E1 income/tax row | Finding | Verdict |
| --- | --- | --- |
| `paycheck_gross` | F-032 (GOVERNED #3) | DIVERGE (DEFINITION+ROUNDING) |
| `paycheck_net` | F-033 | AGREE |
| `taxable_income` | F-034 | UNKNOWN (Q-13 sub-pair) / by-design layers AGREE |
| `federal_tax` | F-035 | AGREE (bracket/calibrated by-design) + UNKNOWN (Q-13 sub-pair) |
| `state_tax` | F-036 | AGREE (rounding-residue + PA-24 = Phase-7) |
| `fica` | F-037 (GOVERNED #1) | DIVERGE (DEFINITION_DRIFT) |
| `pre_tax_deduction` | F-038 | AGREE (ordering invariant HOLDS) + UNKNOWN (Q-13 sub-pair) |
| `post_tax_deduction` | F-039 | AGREE (ordering invariant HOLDS) |
| (standalone governed #2: legacy `calculate_federal_tax`) | F-040 | DEAD_CODE |

**E1 income/tax rows: 8; findings produced: 8 (F-032..F-039), plus F-040 standalone governed.**
Reconciled 1:1.

#### Canonical engine substrate (Read at source this session; cited by every finding below)

`calculate_paycheck`@`paycheck_calculator.py:92-247`, Read in full -- the single canonical producer
of all 8 tokens. Pipeline: gross @133-135
`(_apply_raises(profile,period) / pay_periods).quantize(TWO_PLACES, ROUND_HALF_UP)`; pre-tax
@149-152 (`_calculate_deductions` PRE_TAX id, summed); taxable @155-157
`gross_biweekly - total_pre_tax`, floor 0; tax steps 6-7 @159-214 selected by
`use_calibration = calibration is not None and getattr(calibration,"is_active",False)` @160-163
(bracket ELSE @174-214 vs `apply_calibration` @165-173); post-tax @217-220; net @223-231 single
terminal `.quantize(TWO_PLACES, ROUND_HALF_UP)`. `TWO_PLACES = Decimal("0.01")` @51, `ROUND_HALF_UP`
imported @43 -- the engine is A-01-clean at every quantize (consistent with the A-01 verification
verdict, `09_open_questions.md:37-62`, "every service that names a rounding mode uses it").
`_apply_raises`@`:274-326` terminal quantize HALF_UP @326. `_calculate_deductions` @`:403-460` is
ONE parameterized producer for both pre- and post-tax (distinguished only by `timing_id`); pct
branch @440 `(gross_biweekly * amount).quantize(TWO_PLACES, HALF_UP)`.
`_get_cumulative_wages`@`:480-504` recomputes per-period gross @499-501 with the byte-identical
formula `(_apply_raises(profile,p)/pay_periods).quantize(TWO_PLACES, HALF_UP)`.

`tax_calculator`@`:1-322`, Read in full -- `calculate_federal_withholding`@`:35-170` (Pub 15-T;
per-period @158-164 HALF_UP; subtracts pre_tax @112, std-ded @117-118; validates gross<0 @91-92,
pay_periods<=0 @93-94, negative dependents @97-100); `_apply_marginal_brackets`@`:173-209` (HALF_UP
@209); `calculate_state_tax`@`:240-268` (ID-based NONE check @257 via `ref_cache`, E-15-clean;
annual `(taxable*rate).quantize( TWO_PLACES, HALF_UP)` @266); `calculate_fica`@`:274-321` (SS cap
@300-306, Medicare+surtax @309-318); legacy `calculate_federal_tax`@`:215-234` (F-040).

`calibration_service`@`:1-146`, Read in full -- `apply_calibration`@`:106-145` (calibrated path:
`federal=(taxable*rate).quantize(HALF_UP)` @133-135, `state` @136-138,
`ss=(gross*rate).quantize(HALF_UP)` @139-141, `medicare` @142-144 --
**no `ss_wage_base`, no `cumulative_wages` parameter**); `derive_effective_rates`@`:34-103` produces
effective RATES (a calibration INPUT: `federal/taxable` @83, `ss/gross` @91, RATE_PLACES 10dp
HALF_UP) -- per the 1.7.8 P1-f/delegate-only rule it is a rate-input producer,
**NOT a comparable income/tax amount producer**; recorded here so P3-reconcile sees it was triaged,
not skipped or mis-compared.

`use_calibration` gate proven mutually exclusive (grep this session:
`grep -rn "calculate_fica\|apply_calibration" app/` -> `calculate_fica` called ONLY at
`paycheck_calculator.py:210` (bracket ELSE, threads `_get_cumulative_wages` @207);
`apply_calibration` called ONLY at `paycheck_calculator.py:167` (the `if use_calibration` branch)).
For one profile-period exactly one of the two tax paths runs -- the bracket-vs-calibrated amount
difference is gated and intentional (catalog E2 `02_concepts.md:3287`: federal/state
bracket-vs-calibrated "DIFFER by design... labeled intentional, NOT drift"), NOT silent drift
between two producers of one number.

---

### Finding F-032: paycheck_gross consistency -- off-engine dti income denominator (GOVERNED)

- Concept: `paycheck_gross` (E1 `02_concepts.md:3237`; Phase-2 Gate F1 dti income seam,
  `02_concepts.md:3358`; the income side of P3-b **F-025** dti_ratio)
- Symptom link: none developer-reported; PHASE 3 REQUIRED per the P2-c analytical frame (an
  aggregation-layer recompute of a single-engine concept) and because it is the `dti_ratio`
  income denominator that F-025 deferred to P2-c/P3-d1.
- Paths compared (every producer pair, `02_concepts.md:1419-1436`):
  - A = canonical `calculate_paycheck`@`paycheck_calculator.py:133-135`.
  - B = off-engine dti base `_load_account_params`@`savings_dashboard_service.py:263-266`.
  - C = FICA-cap re-derivation `_get_cumulative_wages`@`paycheck_calculator.py:499-501`.
  - D = simplified annual `pension_calculator.project_salaries_by_year`@`:78` (retirement
    family; cross-link only -- it feeds `pension_benefit_*`, owned by P3-d2; NOT re-verdicted
    here).
  - Pairs in income/tax scope: A-B (the governed DIVERGE), A-C (AGREE by construction).
- Path A (Read at source): `pay_periods_per_year = profile.pay_periods_per_year or 26` @132;
  `gross_biweekly = (annual_salary / pay_periods_per_year).quantize(TWO_PLACES,
  rounding=ROUND_HALF_UP)` @133-135, where `annual_salary = _apply_raises(profile, period)`
  @125 (post-raise, sorted-raise sub-engine @274-326, terminal HALF_UP @326).
- Path B (Read at source): `_load_account_params`@`savings_dashboard_service.py:201-292`;
  `active_profile = db.session.query(SalaryProfile).filter_by(user_id=user_id,
  is_active=True).first()` @257-261; `salary_gross_biweekly = (Decimal(str(
  active_profile.annual_salary)) / (active_profile.pay_periods_per_year or 26)).quantize(
  Decimal("0.01"))` @263-266. Reads RAW `annual_salary` -- **NO `_apply_raises`**; quantize
  @266 has **NO `rounding=` argument -> Python `decimal` context default `ROUND_HALF_EVEN`
  (banker's)**. Consumed at `compute_dashboard_data`@`:168` `gross_biweekly =
  params["salary_gross_biweekly"]`, `gross_monthly = (gross_biweekly * Decimal("26") /
  Decimal("12")).quantize(_TWO_PLACES, ROUND_HALF_UP)` @170-172, `dti_ratio =
  (total_monthly_payments / gross_monthly * Decimal("100")).quantize(Decimal("0.1"),
  ROUND_HALF_UP)` @173-176; also passed to `calculate_investment_inputs` @535.
- Path C (Read at source): `_get_cumulative_wages`@`:480-504`; `gross = (salary /
  pay_periods_per_year).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)` @499-501 with `salary =
  _apply_raises(profile, p)` @498 and `pay_periods_per_year = profile.pay_periods_per_year or
  26` @489 -- **byte-identical** to Path A's formula.
- Compared dimensions:
  - Raise sequencing: **DIVERGES (A vs B).** A applies `_apply_raises` (compounding recurring
    raises @303-316, flat-before-pct @294-297); B reads RAW `active_profile.annual_salary`
    (`savings_dashboard_service.py:264`) -- zero raise logic. Any profile with one applicable
    `SalaryRaise` produces `A != B`. DEFINITION.
  - Rounding mode: **DIVERGES (A vs B).** A `rounding=ROUND_HALF_UP` @135; B bare
    `.quantize(Decimal("0.01"))` @266 -> banker's `ROUND_HALF_EVEN`. This exact line is the
    A-01 24-omission site `savings_dashboard_service.py:266` (A-01 verification verdict
    **PARTIALLY ACCURATE**, `09_open_questions.md:37-62`; the 24-list at `:42-48` names
    `savings_dashboard_service.py:266`). ROUNDING.
  - Divisor: AGREE -- both `pay_periods_per_year or 26` (A @132, B @265). Not the axis.
  - A vs C: identical formula (`_apply_raises` -> `/ (pp or 26)` -> `.quantize(TWO_PLACES,
    ROUND_HALF_UP)`), same divisor. **AGREE by construction** -- the per-period gross feeding
    the SS-cap YTD base (F-037) equals the canonical gross for the same period (Pair B of
    `02_concepts.md:1429-1432` HOLDS; the cap tracks against an undrifted base).
  - ORM load-context (mandatory): A's `annual_salary` depends on `profile.raises` being
    loaded; `_apply_raises` @288 reads `profile.raises` (lazy-loads if absent -- correct value,
    N+1, not a value divergence). B reads only `active_profile.annual_salary` /
    `.pay_periods_per_year` (scalar columns; load-context-free). The divergence is the raise
    omission + rounding mode, NOT load strategy.
  - Status/scenario/is_deleted/period: not applicable (gross is a salary-profile computation,
    no transaction filters). AGREE (n/a).
- Divergences:
  - A vs B: raw `annual_salary` (no `_apply_raises`) AND banker's-default quantize
    (`savings_dashboard_service.py:264-266`) vs post-raise HALF_UP
    (`paycheck_calculator.py:133-135`). This is the **`dti_ratio` income denominator**: per
    F-025 the DTI numerator is engine-derived `monthly_pi`; here the denominator's base is the
    off-engine recompute -> the displayed DTI ratio rests on a gross that omits raises and
    rounds the other way. SILENT (no error; the user sees a DTI computed against a wrong
    income base). DEFINITION (raise omission) + ROUNDING (mode).
  - A vs C: none -- AGREE by construction (recorded so P3-reconcile sees Pair B was verified,
    not skipped).
- Risk -- worked example (denominator wrong under a scheduled raise): `SalaryProfile`
  `annual_salary = $104,000.00`, `pay_periods_per_year = 26`, one recurring `SalaryRaise`
  `percentage = 0.03` effective this year and reached for the viewed period; debt
  `total_monthly_payments = $2,400.00`.
  - Path A canonical gross: `_apply_raises` -> `104000 * 1.03 = 107120.00` (quantize HALF_UP
    @326); `gross_biweekly = (107120.00 / 26).quantize(0.01, HALF_UP) = 4120.0000 ->
    $4,120.00`. `gross_monthly = (4120.00 * 26 / 12).quantize(0.01, HALF_UP) =
    8926.6666... -> $8,926.67`. Correct `dti = 2400 / 8926.67 * 100 = 26.9%`.
  - Path B off-engine dti base: `salary_gross_biweekly = (Decimal("104000") / 26).quantize(
    Decimal("0.01")) = 4000.00` (RAW salary, no raise). `gross_monthly = (4000.00 * 26 /
    12).quantize(0.01, HALF_UP) = 8666.6666... -> $8,666.67`. Displayed `dti = 2400 /
    8666.67 * 100 = 27.7%`.
  - One profile, one date: the DTI denominator is `$8,666.67` (off-engine, raise dropped)
    instead of the correct `$8,926.67`, and the displayed ratio is `27.7%` vs the correct
    `26.9%`, with no error raised. The rounding-mode half also bites independently: for an
    `annual_salary` whose `/26` lands on a half-cent (e.g. `$50,000 / 26 = 1923.07692...`;
    not a half-cent case) the banker's-vs-HALF_UP split is the documented A-01 class -- the
    raise-omission term dominates the worked example, the rounding term is the A-01-cited
    secondary divergence.
- Verdict: **DIVERGE** -- the A-vs-B raise-omission and rounding-mode divergences hold
  unconditionally from code (no developer answer gates them; A-01 is a resolved developer
  answer that classifies the rounding side as a finding). A-vs-C AGREES by construction.
- If DIVERGE: classification: **DEFINITION_DRIFT** (off-engine recompute drops `_apply_raises`
  -- a different definition of gross) + **ROUNDING_DRIFT** (banker's default vs ROUND_HALF_UP,
  the A-01 `savings_dashboard_service.py:266` 24-list site).
- Open questions for the developer: none new -- A-01 (resolved, PARTIALLY ACCURATE) governs
  the rounding side and the raise omission is provable from code. Cross-link **F-025** (P3-b
  dti_ratio debt side; this finding is its income-denominator counterpart, the seam P2-c
  closed), Phase-2 **Gate F1** (`02_concepts.md:3358`, source-verified), the **A-01
  verification verdict** (`09_open_questions.md:37-62`), **Q-12**
  (`09_open_questions.md:459-512`; the `26/12` factor duplicated at
  `savings_dashboard_service.py:170-172,765` and `savings_goal_service.py:17-18` -- A-12
  proposed, pending; cross-link only, not this finding's verdict), **PA-07** (the canonical
  gross @133-135 is the F-127 biweekly-residue site; the off-engine @266 compounds PA-07 with
  the A-01 mode divergence). Remediation direction: route the dti income base through
  `calculate_paycheck` (post-raise, HALF_UP) rather than the off-engine
  `savings_dashboard_service.py:263-266` recompute.

---

### Finding F-033: paycheck_net consistency (relationship invariant)

- Concept: `paycheck_net` (E1 `02_concepts.md:3238`)
- Symptom link: none (end-of-pipeline)
- Relationship invariant (verbatim, code's actual form, Read at source
  `paycheck_calculator.py:223-231`): `net_pay = (gross_biweekly - total_pre_tax -
  federal_biweekly - state_biweekly - ss_biweekly - medicare_biweekly -
  total_post_tax).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)` -- i.e. `gross - pre_tax -
  federal - state - ss - medicare - post_tax`, **single terminal quantize** over upstream-
  already-quantized components. With `fica := ss + medicare` this is exactly the audit-plan
  section-7 form `net = gross - (federal+state+fica) - pre_tax - post_tax` (catalog
  `02_concepts.md:1466-1480`); no grouping divergence.
- Paths compared: single genuine producer `calculate_paycheck`@`paycheck_calculator.py:92`
  (net @223-231); every other listed "producer" is a delegate CONSUMER (1.7.8 rule). Consumer
  paths Read at source to verify none recomposes net differently:
  - `recurrence_engine._get_transaction_amount`@`:761-765`: calls
    `paycheck_calculator.calculate_paycheck(... calibration=calibration)` @761-763 and
    `return breakdown.net_pay` @765 -- pure pass-through (the catalog cited `:720`; the
    `return breakdown.net_pay` is at `:765`, line drift noted, behavior identical). The
    tax_year fallback @745-756 is a documented same-net-as-salary-page consistency fix, not a
    recompose.
  - `savings_dashboard_service._get_net_biweekly_pay`@`:597-600`: calls `calculate_paycheck`
    @597, `return breakdown.net_pay` @600 -- pure pass-through.
  - `retirement_gap_calculator.calculate_gap`@`:37`: `net_biweekly_pay` is an INPUT parameter
    (catalog P1-f reconciliation `02_concepts.md:1485-1490`) -- CONSUMER, no derivation.
- Compared dimensions: single canonical producer; consumers read `breakdown.net_pay`
  unchanged. Quantization: single terminal HALF_UP @231 (A-01-clean); the residue is the
  documented PA-07/PA-20 sum-of-rounded-components surface (accepted simplification, module
  docstring `paycheck_calculator.py:9-38`), not a cross-producer drift. Bracket-vs-calibrated:
  when `use_calibration` is true the same @223-231 formula consumes `apply_calibration`'s
  four amounts -- the invariant FORM is preserved on both paths; the net VALUE differs by
  design (gated, intentional, catalog E2 `:3287`).
- Divergences: none. No consumer recomposes net from component tokens with a different
  grouping or rounding; all pass through `breakdown.net_pay`.
- Verdict: **AGREE** -- single canonical producer
  `calculate_paycheck@paycheck_calculator.py:223-231` Read at source; the section-7 invariant holds
  in the code's actual form; every consumer path is a verified pass-through.
- Open questions for the developer: none. Cross-link **PA-07/PA-20**
  (`00_priors.md:670,683`; the terminal-quantize residue + the missing full-year net-pay sum
  test -- Phase-7 test-gap, not a Phase-3 producer drift), **PA-24** (26-period net vs annual
  reconciliation -- Phase 7). Phase-7 cross-concept assertion: `net == gross - pre_tax -
  federal - state - ss - medicare - post_tax` expanded with `fica = ss + medicare`, expecting
  the single-terminal-quantize residue.

---

### Finding F-034: taxable_income consistency (multi-definition by layer)

- Concept: `taxable_income` (E1 `02_concepts.md:3239`; catalog E2 `:3279`)
- Symptom link: none (Q-13 anchor)
- Paths compared (the FOUR "taxable" computations, Read at source `paycheck_calculator.py`,
  `tax_calculator.py`, `salary.py:1095` cross-link only):
  - D1 = display token `calculate_paycheck`@`paycheck_calculator.py:155-157` `taxable_biweekly
    = gross_biweekly - total_pre_tax`, floor 0 -- the canonical displayed
    `PaycheckBreakdown.taxable_income` @238.
  - D2 = federal-engine internal `calculate_federal_withholding`@`tax_calculator.py:112,118`
    (`adjusted_income - standard_deduction`, annualized, floor 0) -- a DIFFERENT layer.
  - D3 = state-engine internal `calculate_state_tax`@`tax_calculator.py:263`
    (`annual_gross - std_ded`).
  - D4 = legacy `calculate_federal_tax`@`tax_calculator.py:233` (`annual_gross -
    standard_deduction`, NO pre-tax) -- dead (F-040).
  - Pairs: D1-D2 / D1-D3 (by-design layer differences), D1-vs-calibrate_preview-inline
    (Q-13), D1-D4 (-> F-040).
- Path D1 (Read at source): `paycheck_calculator.py:155-157` -- `taxable_biweekly =
  gross_biweekly - total_pre_tax; if taxable_biweekly < ZERO: taxable_biweekly = ZERO`;
  surfaced as `PaycheckBreakdown.taxable_income` @238. **Canonical for the displayed token.**
- Path D2 (Read at source): `tax_calculator.py:105` `annual_income = gross_pay*pay_periods +
  additional_income`; @112 `adjusted_income = annual_income - pre_tax_deductions -
  additional_deductions`; @117-118 `taxable_income = adjusted_income -
  Decimal(str(bracket_set.standard_deduction))`, floor 0 @119-120 -- annualized, additionally
  subtracts std-ded + W-4 4(b). A federal-internal intermediate, NOT the display token.
- Path D3 (Read at source): `tax_calculator.py:263` `taxable = annual_gross - std_ded`, floor
  0 @264-265 (caller passes `taxable_biweekly*pp` @`paycheck_calculator.py:200`, so pre-tax
  already removed upstream, then state std-ded removed). State-internal, NOT the display token.
- Compared dimensions:
  - Definition/layer: **DIVERGES by design.** D1 = gross - pre_tax (display); D2 = + annualize
    - std-ded + W-4 4(b) (federal internal); D3 = + state std-ded (state internal). The
  catalog (E2 `:3279`) and P2-c primary-path (`02_concepts.md:1568-1574`) record these as distinct
  layered quantities that must NOT be conflated -- a labeled-DEFINITION item, not a drift to fix. The
  displayed token's primary path is determined (D1).
  - Q-13 sub-pair: D1 `gross_biweekly - total_pre_tax` (@155) **vs**
    `salary.calibrate_preview` inline `taxable = data["actual_gross_pay"] -
    bk.total_pre_tax`@`salary.py:1095` -- `bk.total_pre_tax` percentage deductions were
    computed against the PROFILE `gross_biweekly` (`_calculate_deductions` pct @440), not
    `actual_gross_pay`; the two taxable values DIVERGE when the stub gross differs from the
    profile gross (the calibration use case). A-13 is **proposed, pending**
    (`09_open_questions.md:514-559`).
  - Quantization: D1 is a raw subtraction feeding `Numeric(12,2)` display; no quantize axis.
    A-01 n/a here.
- Divergences:
  - D1 vs D2 / D1 vs D3: layered-DEFINITION by design (catalog-recorded; labeled, not drift).
  - D1 vs calibrate_preview inline (`salary.py:1095`): real per-Q-13 base divergence when
    actual gross != profile gross. Verdict gated on Q-13 (A-13 proposed).
  - D1 vs D4 (legacy `:233`, no pre-tax): governed dead-code -> **F-040**.
- Verdict: **AGREE** for the displayed-token primary path (D1
  `paycheck_calculator.py:155-157`, single canonical producer, Read at source) and the D1-D2 /
  D1-D3 layer differences (DEFINITION-by-design, catalog-labeled). **UNKNOWN** for the
  D1-vs-`salary.calibrate_preview`-inline (`salary.py:1095`) sub-pair -- blocked on **Q-13**
  (`09_open_questions.md:514-559`, A-13 proposed: option A recompute pre-tax against
  `actual_gross_pay` = current code DIVERGE; status-quo = labeled bias). The legacy D4 ->
  F-040.
- If DIVERGE (conditional on Q-13): DEFINITION_DRIFT (calibrate_preview taxable base).
- Open questions for the developer: **Q-13** (governing the calibrate_preview sub-pair).
  Cross-link **F-040** (legacy D4 dead-code), **PA-23** (`00_priors.md:686`; the
  federal-internal D2 is the Pub 15-T Steps 1-3 base whose exact value PA-23's tests do not
  pin -- Phase 7), **PA-02** (`00_priors.md:665`; bracket rate/threshold schema range vs DB
  CHECK -- Phase 7/8, not a Phase-3 producer divergence).

---

### Finding F-035: federal_tax consistency (bracket vs calibrated, gated)

- Concept: `federal_tax` (E1 `02_concepts.md:3240`; catalog E2 `:3287`)
- Symptom link: none
- Paths compared:
  - A = canonical bracket engine `calculate_federal_withholding`@`tax_calculator.py:35-170`,
    selected by `calculate_paycheck`@`paycheck_calculator.py:184-195` (`federal_biweekly`).
  - B = calibrated `apply_calibration`@`calibration_service.py:133-135`
    (`(taxable*effective_federal_rate).quantize(TWO_PLACES, HALF_UP)`), selected by the
    `use_calibration` gate @`paycheck_calculator.py:160-173`.
  - C = legacy `calculate_federal_tax`@`tax_calculator.py:215` (dead -> F-040).
  - D = year-end annual federal total (`analytics.year_end_tab`) -- aggregation-layer sum of
    per-period A/B outputs.
  - Sub-pair: A vs `salary.calibrate_preview` Q-13 inline taxable @`salary.py:1095`.
  - `derive_effective_rates`@`calibration_service.py:83` produces the effective federal RATE
    (a calibration INPUT, 1.7.8 rule) -- NOT a comparable `federal_tax` amount producer;
    triaged, not compared.
- Path A (Read at source): `tax_calculator.py:158-164` -- `per_period_withholding =
  (annual_tax_after_credits / pay_periods) + extra_withholding`, then
  `.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)`. A-01-clean.
- Path B (Read at source): `calibration_service.py:133-135` -- `(taxable * federal_rate
  ).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)`. A-01-clean.
- Compared dimensions:
  - Path selection: **mutually exclusive** -- `use_calibration` gate
    (`paycheck_calculator.py:160-163`), proven by grep this session (`apply_calibration` sole
    caller `:167`; bracket ELSE `:184`). For one profile-period exactly one runs.
  - Definition: A bracket Pub 15-T vs B `taxable*effective_rate`: DIFFER by design when
    calibration active (catalog E2 `:3287` "labeled intentional, NOT drift"; primary path
    P2-c `02_concepts.md:1635-1639`). Not silent drift.
  - Aggregation (D): year-end annual total is `sum` over the SAME per-period engine outputs
    -> consistent BY CONSTRUCTION; PA-24 ("no test computes 26-period total vs annual
    liability", `00_priors.md:687`) is a Phase-7 test-gap, not a Phase-3 producer divergence.
  - Quantization: A @162-164, B @133-135 both ROUND_HALF_UP. A-01-clean (NOT in the 24-list).
  - ORM load-context: A depends on `bracket_set` being loaded (`load_tax_configs`); B on the
    `calibration` object. Both are explicit caller-supplied configs (not a relationship-load
    value divergence).
- Divergences:
  - A vs B: gated/by-design (labeled intentional). Not drift.
  - A vs calibrate_preview Q-13 inline (`salary.py:1095`): the Q-13 taxable-base divergence
    propagates into effective rates -> a real divergence gated on Q-13.
  - A vs C (legacy): -> F-040.
- Verdict: **AGREE** for the bracket-vs-calibrated producer pair (gated, mutually exclusive,
  by-design labeled per catalog E2; both A-01-clean, Read at source) and for the year-end
  aggregation (consistent by construction). **UNKNOWN** for the A-vs-`calibrate_preview`-Q-13
  sub-pair -- blocked on **Q-13**. Legacy C -> F-040.
- If DIVERGE (conditional on Q-13): DEFINITION_DRIFT (calibrate_preview effective-rate base).
- Open questions for the developer: **Q-13** (governing the calibrate_preview sub-pair).
  Cross-link **F-040** (legacy dead-code), **PA-23** (`00_priors.md:686`; seven tax tests
  range/directional vs exact Pub 15-T -- Phase 7), **PA-24** (`00_priors.md:687`; 26-period
  vs annual reconciliation -- Phase 7), **PA-02** (`00_priors.md:665`; tax-rate schema-range
  vs DB CHECK -- Phase 7/8).

---

### Finding F-036: state_tax consistency (P2-c override of 1.7.4 single-path)

- Concept: `state_tax` (E1 `02_concepts.md:3241`; catalog E2 `:3287`; P2-c override of
  1.7.4's single-path classification)
- Symptom link: none
- Paths compared:
  - A = canonical `calculate_state_tax`@`tax_calculator.py:240-268` (annual), de-annualized by
    `calculate_paycheck`@`paycheck_calculator.py:199-204` (`state_biweekly`).
  - B = calibrated `apply_calibration`@`calibration_service.py:136-138`
    (`(taxable*effective_state_rate).quantize(TWO_PLACES, HALF_UP)`), gated by
    `use_calibration`.
  - D = year-end state total -- aggregation-layer sum of per-period A/B.
- Path A (Read at source): `tax_calculator.py:257` ID-based NONE check
  (`state_config.tax_type_id == ref_cache.tax_type_id(TaxTypeEnum.NONE)` -> `ZERO`, E-15-clean,
  NOT a name string); @266 annual `(taxable * rate).quantize(TWO_PLACES,
  rounding=ROUND_HALF_UP)`; then `calculate_paycheck`@`:202-204` `state_biweekly =
  (state_annual / pay_periods_per_year).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)` --
  **double quantize** (annual rounded @266, then per-period rounded @202-204).
- Path B (Read at source): `calibration_service.py:136-138` -- **single** biweekly quantize.
- Compared dimensions:
  - Path selection: mutually exclusive (`use_calibration` gate). A bracket vs B calibrated
    DIFFER by design (catalog E2 `:3287`).
  - Quantization: A is a double-quantize (annual @266 then de-annualize @202-204); B single
    @136-138. Both name `ROUND_HALF_UP` (A-01-clean -- neither `:266`/`:202-204`/`:136-138` is
    in the A-01 24-list, `09_open_questions.md:42-48`). The double-quantize introduces a
    sub-cent rounding RESIDUE within the bracket path -- the documented PA-07/PA-20 accepted-
    simplification class (sum-of-rounded), not a cross-producer silent drift (A and B never
    both run for one profile-period).
  - Aggregation (D): year-end state total sums the SAME per-period engine output -> consistent
    by construction; PA-24 is a Phase-7 test-gap.
- Divergences: none cross-producer. The ROUNDING/SCOPE drift-class the E1 register flags is
  resolved on source-read as (i) the gated by-design bracket-vs-calibrated amount difference
  (labeled intentional, catalog E2 `:3287`) and (ii) the bracket-path double-quantize residue
  (PA-07 class, accepted simplification) -- neither is a silent drift between two producers of
  one number.
- Verdict: **AGREE** -- single canonical engine `calculate_state_tax@tax_calculator.py:240-268`
  (Read at source), de-annualized once by `calculate_paycheck:202-204`; the calibrated path is
  a gated by-design override; the annual->per-period double-quantize is the documented PA-07
  rounding-residue class, not a Phase-3 cross-producer divergence. Recorded in full (not
  skipped) per the P2-c 1.7.4-override mandate so P3-reconcile sees the override was honored.
- If DIVERGE: n/a (the rounding residue is a Phase-7/PA-07 note; the gated difference is
  by-design).
- Open questions for the developer: none. Cross-link **PA-02** (`00_priors.md:665`;
  `StateTaxConfig.flat_rate` `Numeric(5,4)` Marshmallow `Range(0,100)` vs DB `CHECK(0..1)`,
  the named F-014 "state tax rates" field -- Phase 7/8 schema, not Phase-3 producer drift),
  **PA-07/PA-20** (double-quantize residue -- Phase 7), **PA-24** (`00_priors.md:687`;
  year-end vs 26-period -- Phase 7).

---

### Finding F-037: fica consistency -- SS wage-base cap bypass on the calibration path (GOVERNED)

- Concept: `fica` (E1 `02_concepts.md:3242`; catalog E2 `:3286`; **concrete PA-21
  confirmation AND extension**, `00_priors.md:684`)
- Symptom link: none developer-reported; PA-21 is the open prior-audit finding this confirms.
- Intended definition (catalog `02_concepts.md:1715-1719`, IRS rule): per-period FICA = Social
  Security **capped at `ss_wage_base`** + Medicare (base on all gross + 0.9% surtax above
  `medicare_surtax_threshold`), tracked via cumulative YTD wages. Once cumulative wages reach
  `ss_wage_base`, SS accrual MUST stop.
- Paths compared:
  - A = bracket/engine path `calculate_fica`@`tax_calculator.py:274-321`, driven by
    `calculate_paycheck`@`paycheck_calculator.py:206-214` (threads
    `_get_cumulative_wages`@`:480-504`).
  - B = calibrated path `apply_calibration`@`calibration_service.py:139-144`, selected by the
    `use_calibration` gate @`paycheck_calculator.py:160-173`.
  - Pair A-B.
- Path A (Read at source `tax_calculator.py:299-307`): SS cap **ENFORCED** --
  `if cumulative >= ss_wage_base: ss_tax = ZERO` @300-301;
  `elif cumulative + gross > ss_wage_base: ss_taxable = ss_wage_base - cumulative; ss_tax =
  (ss_taxable * ss_rate).quantize(TWO_PLACES, ROUND_HALF_UP)` @302-304 (the partial-crossing
  period); `else: ss_tax = (gross * ss_rate).quantize(...)` @305-306. `ss_wage_base =
  Decimal(str(fica_config.ss_wage_base))` @294. Cumulative YTD threaded by
  `calculate_paycheck`@`:206-212` from `_get_cumulative_wages`@`:480-504` (which recomputes
  per-period gross @499-501 with the canonical formula -- F-032 Pair C AGREE, so the cap
  tracks against an undrifted base).
- Path B (Read at source `calibration_service.py:139-144`): `"ss": (gross *
  ss_rate).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)` -- flat `gross *
  effective_ss_rate` **every period**. `apply_calibration`'s signature
  (`:106` `def apply_calibration(gross_biweekly, taxable_biweekly, calibration)`) has **no
  `cumulative_wages` parameter and no `ss_wage_base` reference** (grep this session:
  `grep -rn "ss_wage_base\|cumulative_wages" app/services/` -> zero matches in
  `calibration_service.py`; `ss_wage_base` consumed ONLY in `tax_calculator.calculate_fica`).
  SS keeps accruing past the wage base on the calibration path.
- Compared dimensions:
  - SS wage-base cap: **DIVERGES.** A enforces (`tax_calculator.py:300-306`); B has no cap and
    no cumulative-wage input (`calibration_service.py:139-144`).
  - Path selection: gated `use_calibration` -- but unlike federal/state (where the
    bracket-vs-calibrated amount difference is benign/by-design), here the calibrated path
    **silently drops a hard IRS invariant**: a high earner who calibrated from a pay stub
    accrues SS tax in every period of the year, including periods after YTD wages exceed
    `ss_wage_base`, where the correct SS is `$0`. This is the catalog's "DEFINITION
    DISCREPANCY between the two fica flavors AND a concrete PA-21 confirmation"
    (`02_concepts.md:1730-1735`), not a benign labeled-intentional difference.
  - Quantization: both ROUND_HALF_UP (A @304/306, B @139-141) -- A-01-clean; rounding is NOT
    the axis. The divergence is the missing cap, dollars not cents.
  - ORM load-context: A's cap correctness depends on `calculate_paycheck` threading
    `_get_cumulative_wages` (it always does, @207); B structurally cannot take cumulative
    wages (no parameter). Not a load-strategy divergence -- a structural omission.
- Divergences: B (`apply_calibration`, `calibration_service.py:139-141`) never zeroes SS
  after the wage base; A (`calculate_fica`, `tax_calculator.py:300-306`) does. For a high
  earner on the calibration path, SS accrues for the entire year. SILENT (no error; the net
  pay and FICA line are simply wrong for the over-cap periods) -> **DEFINITION_DRIFT**.
- Risk -- worked example (SS accrual past `ss_wage_base` on the calibration path): 2026
  `FicaConfig.ss_wage_base = $184,500.00` (the seeded value, `auth_service.py:412`),
  `ss_rate = 0.062`. Employee `annual_salary = $312,000.00`, `pay_periods_per_year = 26` ->
  per-period gross `$12,000.00`; calibration active with `effective_ss_rate = 0.062`.
  - Cumulative wages reach `ss_wage_base` partway through period 16 (`15 * 12,000 =
    $180,000 < 184,500`; `16 * 12,000 = $192,000 > 184,500`).
  - **Path A (bracket, cap enforced):** period 16 is the partial-crossing period --
    `cumulative = $180,000`, `ss_taxable = 184,500 - 180,000 = $4,500`, `ss = (4500 *
    0.062).quantize(0.01, HALF_UP) = $279.00` (`tax_calculator.py:302-304`). Periods 17-26:
    `cumulative >= ss_wage_base` -> `ss_tax = ZERO` (`:300-301`). Year SS total `= 15 *
    (12000*0.062) + 279.00 + 0 = 15*744.00 + 279.00 = $11,439.00` (= `184,500 * 0.062`).
  - **Path B (calibrated, NO cap):** every period `ss = (12,000 * 0.062).quantize(0.01,
    HALF_UP) = $744.00` (`calibration_service.py:139-141`), all 26 periods. Period 16 SS =
    `$744.00` (vs A's `$279.00`); periods 17-26 SS = `$744.00` each (vs A's `$0.00`). Year SS
    total `= 26 * 744.00 = $19,344.00` -- `$7,905.00` over the correct `$11,439.00`, i.e. SS
    accrued on `$127,500.00` of wages above the cap that IRS rules exempt.
  - One employee, one year: if the profile is bracket-based the FICA line and net pay are
    correct; the moment the developer calibrates from a pay stub (the documented calibration
    use case) the SS line keeps charging $744.00/period after the wage base, overstating FICA
    and understating net pay by `$744.00` per over-cap period, with no error raised.
- Verdict: **DIVERGE** -- the calibration-path SS-cap bypass is provable from code
  (`calibration_service.py:139-144` has no `ss_wage_base`/`cumulative_wages`; grep-confirmed
  zero `ss_wage_base` in `calibration_service.py`) and the FICA intended definition (SS stops
  at the wage base) makes the unenforced invariant a finding regardless of the gated path
  (the gate makes it reachable for exactly the calibration use case, not benign). Holds
  independently of any pending Q.
- If DIVERGE: classification: **DEFINITION_DRIFT** (the two `fica` flavors mean different
  things -- one caps SS, one does not), with **PLAN/test linkage to PA-21** (`00_priors.md:684`,
  open; this is a concrete confirmation AND extension -- PA-21 said the cap is untested on
  both paths; this finding shows it is unenforced on the calibration path).
- Open questions for the developer: none new -- the IRS SS wage-base cap is a hard invariant
  (catalog `02_concepts.md:1715-1719`), not a "what is intended" ambiguity; the calibration
  path silently violating it is a finding by definition. Cross-link **PA-21**
  (`00_priors.md:684`), the P2-c `fica` primary-path (`02_concepts.md:1761-1765`), catalog E2
  (`02_concepts.md:3286`), **PA-24** (`00_priors.md:687`; 26-period incl. cap-crossover vs
  annual -- the Phase-7 test that would catch this), **PA-02** (`00_priors.md:665`;
  `FicaConfig.ss_rate`/`medicare_rate` `Numeric(5,4)` schema-range -- Phase 7/8). Remediation
  direction: thread cumulative YTD wages + `ss_wage_base` into `apply_calibration` (or zero
  the calibrated SS once YTD >= `ss_wage_base`) so the calibration path honors the same cap
  as `tax_calculator.calculate_fica`.

---

### Finding F-038: pre_tax_deduction consistency (ordering invariant + Q-13 pct base)

- Concept: `pre_tax_deduction` (E1 `02_concepts.md:3243`)
- Symptom link: none
- Ordering-dependency invariant (definitional, catalog `02_concepts.md:1794-1801`):
  `pre_tax_deduction` MUST be computed and subtracted from gross BEFORE `taxable_income` and
  tax computation. Verified at source `paycheck_calculator.py`: pre-tax computed step 4
  @149-152 (`_calculate_deductions(... pre_tax_id ...)`, summed @152) -> `taxable_biweekly =
  gross_biweekly - total_pre_tax` step 5 @155 -> bracket federal annualizes `annual_pre_tax =
  total_pre_tax * pay_periods_per_year` @176 and passes it to
  `calculate_federal_withholding` (subtracted @`tax_calculator.py:112`); state receives
  `taxable_biweekly * pay_periods_per_year` @200; FICA on full `gross_biweekly` @210-212
  (FICA is correctly on gross, not taxable). **No producer applies a pre-tax deduction after
  tax computation -- invariant HOLDS.**
- Paths compared: single parameterized producer
  `_calculate_deductions`@`paycheck_calculator.py:403-460` invoked with the PRE_TAX timing id by
  `calculate_paycheck`@`:149`. Q-13 sub-pair: pct branch @440
  `(gross_biweekly * amount).quantize(TWO_PLACES, ROUND_HALF_UP)` uses the PROFILE `gross_biweekly`,
  vs `salary.calibrate_preview` consuming that profile-gross-based `bk.total_pre_tax` against
  `actual_gross_pay`@`salary.py:1095` (A-13 proposed, pending).
- Compared dimensions: single producer (pre/post share `_calculate_deductions`, distinguished
  by `timing_id` -- DRY-correct, Phase-6 note). Ordering invariant: HOLDS (step 4 before step
  5 before steps 6-7, Read at source). Quantization: pct @440-442 and inflation @451-452 both
  ROUND_HALF_UP (A-01-clean; PA-07/PA-20 residue class only). The only divergence axis is the
  Q-13 percentage-base mismatch (profile gross vs actual stub gross).
- Divergences: none in the producer or the ordering invariant. The Q-13 pct-base divergence
  (profile `gross_biweekly` @440 vs `actual_gross_pay` @`salary.py:1095`) is real but its
  verdict is gated on Q-13.
- Verdict: **AGREE** for the canonical producer `_calculate_deductions@paycheck_calculator.py:403`
  (Read at source) and the ordering invariant (HOLDS -- pre-tax subtracted before taxable/tax,
  proven @149-155-176-200). **UNKNOWN** for the Q-13 pct-base sub-pair (profile-gross vs
  actual-gross percentage deduction) -- blocked on **Q-13** (`09_open_questions.md:514-559`,
  A-13 proposed).
- If DIVERGE (conditional on Q-13): DEFINITION_DRIFT (percentage deduction base).
- Open questions for the developer: **Q-13** (governing the pct-base sub-pair). Cross-link
  **F-034** (the calibrate_preview taxable that consumes this `total_pre_tax`), **PA-07/PA-20**
  (`00_priors.md:670,683`; pct/inflation per-period quantize feeds the net residue -- Phase 7),
  **PA-23** (`00_priors.md:686`; `annual_pre_tax = total_pre_tax*pp`@`:176` is the Pub 15-T
  Step 2 input PA-23 leaves unpinned -- Phase 7).

---

### Finding F-039: post_tax_deduction consistency (ordering invariant)

- Concept: `post_tax_deduction` (E1 `02_concepts.md:3244`)
- Symptom link: none
- Ordering-dependency invariant (definitional, catalog `02_concepts.md:1839-1844`):
  `post_tax_deduction` MUST be applied AFTER tax computation and MUST NOT reduce
  `taxable_income`. Verified at source `paycheck_calculator.py`: post-tax computed step 8
  @217-220 (`_calculate_deductions(... post_tax_id ...)`, summed @220) AFTER federal/state/FICA
  steps 6-7 @159-214, and subtracted ONLY inside `net_pay` step 9 @230
  (`... - total_post_tax).quantize(...)` @223-231); it is NEVER subtracted from
  `taxable_biweekly`@`:155` (which is `gross_biweekly - total_pre_tax` only). **No producer
  applies a post-tax deduction before tax computation -- invariant HOLDS.**
- Paths compared: same single parameterized producer
  `_calculate_deductions`@`paycheck_calculator.py:403` as F-038, invoked with the POST_TAX timing id
  @`:217` (DRY-correct -- one core, two timing ids; Phase-6 note recorded, not actioned). No second
  producer.
- Compared dimensions: single producer; the only Phase-3-relevant axis is the ordering
  invariant (Read at source: step 8 @217-220 strictly after steps 6-7 @159-214; subtracted
  only in net @230; absent from taxable @155). Quantization pct/inflation @440-442,@451-452
  ROUND_HALF_UP (A-01-clean; PA-07/PA-20 residue only).
- Divergences: none. Invariant HOLDS.
- Verdict: **AGREE** -- single canonical producer `_calculate_deductions@paycheck_calculator.py:403`
  (POST_TAX timing id, `calculate_paycheck:217`, Read at source); the post-tax-after-tax
  ordering invariant holds (post-tax never reduces `taxable_biweekly:155`, subtracted only in
  `net_pay:230`).
- If DIVERGE: n/a.
- Open questions for the developer: none. Cross-link **PA-07/PA-20** (`00_priors.md:670,683`;
  per-period quantize feeds the net terminal-quantize residue -- Phase 7), **PA-22**
  (`00_priors.md:685`; deduction input edge cases inactive/zero/pct-of-zero-gross in
  `_calculate_deductions:422-458` -- Phase 7 test gap). Phase-6 DRY note: `pre_tax_deduction`
  and `post_tax_deduction` share `_calculate_deductions` (one parameterized core) -- correct,
  recorded for `06_dry_solid.md`, not actioned (hard rule: no code change).

---

### Finding F-040: legacy calculate_federal_tax dead-code (standalone GOVERNED #2)

- Concept: `taxable_income` / `federal_tax` definition discrepancy (catalog flags it under
  both, `02_concepts.md:1536-1539,1651-1652`, E2 `:3279`); standalone finding (no own E1 row,
  analogous to P3-a F-009 / P3-b F-026 standalone findings).
- Symptom link: none.
- The discrepancy (Read at source `tax_calculator.py:215-234`): legacy
  `calculate_federal_tax(annual_gross, bracket_set)` computes `taxable =
  Decimal(str(annual_gross)) - Decimal(str(bracket_set.standard_deduction))` @233 and returns
  `_apply_marginal_brackets(taxable, bracket_set.brackets)` @234 -- it subtracts the standard
  deduction but **does NOT subtract `pre_tax_deduction`** (the canonical
  `calculate_federal_withholding` subtracts annualized pre-tax @`tax_calculator.py:112`) and
  returns an **ANNUAL** figure, not the per-period withholding the canonical engine produces.
  If live, it would over-state taxable income (no pre-tax adjustment) -- a DEFINITION
  discrepancy vs the canonical `federal_tax`/`taxable_income`.
- LIVENESS rule (governed #2 mandate): `grep -rn "calculate_federal_tax" app/` re-run THIS
  session returns **exactly one line: the definition** `app/services/tax_calculator.py:215:def
  calculate_federal_tax(annual_gross, bracket_set):`. `grep -rn "calculate_federal_tax" app/ |
  grep -v "def calculate_federal_tax" | wc -l` -> **0**. The only other reference anywhere is
  `tests/test_services/test_tax_calculator.py` (a test exercising the legacy interface; not an
  `app/` consumer, not a routed/live code path). **Zero `app/` consumers confirmed.**
- Compared dimensions: n/a -- there is no second live producer/consumer to compare; the
  function is unreachable in production. The "discrepancy" is latent, not a live divergence.
- Divergences: the legacy-vs-canonical taxable-base difference would be a DEFINITION_DRIFT IF
  the function were reachable; it is not.
- Verdict: **DEAD_CODE** -- per the LIVENESS rule, with zero `app/` consumers grep-confirmed
  this session, the legacy `calculate_federal_tax`@`tax_calculator.py:215-234` is NOT a live
  DIVERGE. It is recorded as a **Phase 8 cleanup finding** (dead code carrying a
  no-pre-tax/annual definition that contradicts the canonical engine -- a future-drift trap if
  a new caller wires it in).
- If DIVERGE: n/a (dead code; not a live divergence by the LIVENESS rule -- the F-017
  dead-code class precedent: a divergence in unreachable code is DEAD_CODE, not DIVERGE).
- Open questions for the developer: none. Remediation direction (Phase 8, report-only):
  delete `calculate_federal_tax`@`tax_calculator.py:215-234` (and its test) or, if retained
  as a public API, make it subtract pre-tax and document why it returns annual. Cross-link
  **F-034** (`taxable_income` D4), **F-035** (`federal_tax` legacy path C), catalog E2
  (`02_concepts.md:3279`), the P2-c QC note (`02_concepts.md:1346-1347`).

---

### P3-d1 verification (a-f)

- **(a) Every income/tax E1 row maps to a finding.** E1 income/tax rows
  (`02_concepts.md:3237-3244`) = 8: `paycheck_gross`->F-032, `paycheck_net`->F-033,
  `taxable_income`->F-034, `federal_tax`->F-035, `state_tax`->F-036, `fica`->F-037,
  `pre_tax_deduction`->F-038, `post_tax_deduction`->F-039 (1:1); plus standalone governed
  legacy `calculate_federal_tax`->F-040. Zero income/tax single-path internal-verify rows
  (the `02_concepts.md:3259-3271` list is loan/retirement/savings/transfer only).
  **E1 income/tax rows: 8; findings: 8 (F-032..F-039), plus F-040 standalone governed.**
  Reconciled 1:1. **HOLDS.**
- **(b) All three mandatory governed findings present, each with a worked example.**
  #1 FICA SS wage-cap bypass -> **F-037** (worked: `ss_wage_base = $184,500`, per-period gross
  `$12,000`; bracket path year SS `$11,439.00` with cap, calibrated path `$19,344.00` with
  NO cap -- SS accrual of `$744.00`/period continuing past the cap on periods 17-26, the
  calibration path explicitly shown accruing past `ss_wage_base`). #3 off-engine
  `paycheck_gross` dti base -> **F-032** (worked: `annual_salary $104,000` + recurring `3%`
  raise; canonical `gross_monthly $8,926.67` / dti `26.9%` vs off-engine `$8,666.67` / dti
  `27.7%` -- the denominator differing under a scheduled raise, plus the A-01 banker's-mode
  side). #2 legacy `calculate_federal_tax` -> **F-040** (grep cited; DEAD_CODE). **HOLDS.**
- **(c) The legacy `calculate_federal_tax` grep run and cited; DEAD_CODE only on zero
  consumers.** `grep -rn "calculate_federal_tax" app/` -> 1 line (the def
  `tax_calculator.py:215`); non-def `app/` refs = 0 (piped `grep -v | wc -l`). Sole other ref
  `tests/test_services/test_tax_calculator.py` (test, not a live `app/` consumer). Verdict
  DEAD_CODE with the grep evidence cited in F-040. **HOLDS.**
- **(d) Every one-line AGREE names the canonical producer file:line actually Read this session.**
  F-033 -> `calculate_paycheck@paycheck_calculator.py:223-231` (whole module Read 1-505) + consumer
  pass-throughs `recurrence_engine.py:761-765`, `savings_dashboard_service.py:597-600` Read. F-036
  -> `calculate_state_tax@tax_calculator.py:240-268`
  - `calculate_paycheck:202-204` (whole module Read 1-322 / 1-505). F-039 ->
  `_calculate_deductions@paycheck_calculator.py:403-460` + `:217-230`. F-034/F-035/F-038
  AGREE-portions name D1 `paycheck_calculator.py:155-157`, bracket `tax_calculator.py:35-170`,
  `_calculate_deductions:403` respectively (all Read at source). **HOLDS.**
- **(e) Self spot-check -- 5 random findings re-Read at source this session (mix
  AGREE/DIVERGE/DEAD_CODE):**
  1. **F-032 DIVERGE** "`savings_dashboard_service.py:263-266` raw `annual_salary`, no
     `rounding=` (banker's)": re-Read `:263-266` -> `salary_gross_biweekly =
     (Decimal(str(active_profile.annual_salary)) / (active_profile.pay_periods_per_year or
     26)).quantize(Decimal("0.01"))` -- raw salary, no `_apply_raises`, no `rounding=` arg.
     **Confirmed** (matches A-01 24-list `09_open_questions.md:47`).
  2. **F-037 DIVERGE** "`calibration_service.py:139-144` no `ss_wage_base`/`cumulative_wages`":
     re-Read `calibration_service.py:106,139-144` -> `def apply_calibration(gross_biweekly,
     taxable_biweekly, calibration)` (no cumulative param); `"ss": (gross *
     ss_rate).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)` -- flat, no cap; grep
     `ss_wage_base` in `calibration_service.py` -> 0. **Confirmed.**
  3. **F-040 DEAD_CODE** "`tax_calculator.py:233` no pre-tax; zero `app/` consumers": re-Read
     `tax_calculator.py:215-234` -> `taxable = Decimal(str(annual_gross)) -
     Decimal(str(bracket_set.standard_deduction))` @233 (no pre_tax term); grep non-def `app/`
     refs = 0. **Confirmed.**
  4. **F-033 AGREE** "net single terminal quantize, consumers pass through": re-Read
     `paycheck_calculator.py:223-231` -> `net_pay = (gross_biweekly - total_pre_tax -
     federal_biweekly - state_biweekly - ss_biweekly - medicare_biweekly -
     total_post_tax).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)`; `recurrence_engine.py:765`
     `return breakdown.net_pay`. **Confirmed** (form == section-7; pass-through).
  5. **F-039 AGREE** "post-tax step 8 after taxes, only in net, never in taxable": re-Read
     `paycheck_calculator.py:155,217-220,230` -> taxable @155 = `gross_biweekly -
     total_pre_tax` (no post-tax term); post-tax computed @217-220; subtracted only @230 in
     `net_pay`. **Confirmed.** Pass rate: **5/5.**
- **(f) Every UNKNOWN names the blocking Q-NN.** F-034 D1-vs-calibrate_preview-inline ->
  **Q-13** (`09_open_questions.md:514-559`, A-13 proposed). F-035
  A-vs-calibrate_preview-inline -> **Q-13**. F-038 pct-base sub-pair -> **Q-13**. No UNKNOWN
  lacks a governing filed Q (Q-13 is the sole blocker in this family; the highest open Q
  remains Q-17 from P3-b -- P3-d1 raised no new Q-NN, consistent with P2-c
  `02_concepts.md:1324-1329`). **HOLDS.**

P3-d1 complete (income/tax family, F-032..F-040). Phase 3 is NOT complete -- P3-d2, P3-watchlist,
and P3-reconcile remain; P3-reconcile is the Phase-3 completion gate. P3-a / P3-b / P3-c / P2 / P1 /
priors content unmodified (append-only; only the `Finding IDs used` header was updated this
session). No source, test, or migration file modified this session. Not committed; developer reviews
between sessions.

---

## P3-d2: growth / retirement / savings / year-end family + the two orphans (F-041..F-056)

Session P3-d2 (part 4b, the FINAL family session of Phase 3), 2026-05-16. Scope: `apy_interest`,
`growth`, `employer_contribution`, `contribution_limit_remaining`, `ytd_contributions`,
`goal_progress`, `emergency_fund_coverage_months`, `cash_runway_days`, `pension_benefit_annual`,
`pension_benefit_monthly`, `year_summary_jan1_balance`, `year_summary_dec31_balance`,
`year_summary_principal_paid`, `year_summary_growth`, `year_summary_employer_total`, and the two
Gate-A orphans `entry_sum_total` / `entry_remaining`. Read-only (audit plan section 0). Every
verdict below was produced by Reading the ENTIRE producing function at the cited `file:line` in THIS
session, not inferred from the P2 catalog or inventory. No developer-reported symptom is owned here
(symptoms #1-#5 are balance/loan/income). Q-08 (entry-progress base) and Q-15 (multi-account
dispatcher) are PENDING -> rows gated on them get verdict UNKNOWN with the blocking Q named; no
guessed verdict (hard rule 5). LIVENESS rule applied. The three token-overloaded concepts (`growth`,
`year_summary_growth`, `goal_progress`) are SPLIT per E2 BEFORE any pairwise comparison (F-027
consolidated pattern).

### E1 P3-d2-row reconciliation (verification a)

E1 register (`02_concepts.md:3215-3271`) rows owned by P3-d2:

- **11 multi-path** (`02_concepts.md:3245-3257`): `apy_interest`, `growth`,
  `employer_contribution`, `goal_progress`, `entry_sum_total`, `entry_remaining`,
  `year_summary_jan1_balance`, `year_summary_dec31_balance`, `year_summary_principal_paid`,
  `year_summary_growth`, `year_summary_employer_total`.
- **6 single-path internal-verify** (`02_concepts.md:3259-3271`):
  `contribution_limit_remaining`, `ytd_contributions`, `emergency_fund_coverage_months`,
  `cash_runway_days`, `pension_benefit_annual`, `pension_benefit_monthly`.

= **17 E1 rows.** Findings F-041..F-056 = **16 findings** (15 1:1 +
**F-056 consolidates the two orphans `entry_sum_total`+`entry_remaining`** into one section-3
finding, as the session mandate permits). Mapped 1:1 below.

| E1 row | Finding | Verdict |
| --- | --- | --- |
| `apy_interest` | F-041 | AGREE (single engine; PA-06 365 uniform across producers) |
| `growth` | F-042 (consolidated G1-G4) | DIVERGE (PA-04 cross-anchor SWR; SILENT) |
| `employer_contribution` | F-043 | DIVERGE (SILENT: card uncapped vs chart limit-capped) |
| `contribution_limit_remaining` | F-044 | AGREE (single-path, no recompute) |
| `ytd_contributions` | F-045 | AGREE (single producer; bypass contract-safe) |
| `goal_progress` | F-046 (consolidated GP1-GP2) | GP1 AGREE / GP2 UNKNOWN (Q-08) |
| `emergency_fund_coverage_months` | F-047 | AGREE (single producer; internally consistent) |
| `cash_runway_days` | F-048 | AGREE (balance base == dashboard checking_balance) |
| `pension_benefit_annual` | F-049 | AGREE (single producer; A-01-clean) |
| `pension_benefit_monthly` | F-050 | AGREE (= annual/12, documented; pass-through) |
| `year_summary_jan1_balance` | F-051 | UNKNOWN (Q-15; cross-ref F-006) |
| `year_summary_dec31_balance` | F-052 | UNKNOWN (Q-15; cross-ref F-006/F-007) |
| `year_summary_principal_paid` | F-053 | AGREE-by-construction (cross-ref F-017) |
| `year_summary_growth` | F-054 (consolidated YG1-YG3) | YG1 UNKNOWN (Q-15) / YG2,YG3 AGREE-by-construction |
| `year_summary_employer_total` | F-055 | DIVERGE (inherits F-043) + UNKNOWN (Q-15) |
| `entry_sum_total` | F-056 | AGREE (Python==SQL arithmetic; SCOPE by-design) |
| `entry_remaining` | F-056 | UNKNOWN (Q-08; cross-ref F-028) |

**E1 rows this family: 17; findings: 16 (F-041..F-056, F-056 consolidates the 2 orphans).**
Reconciled 1:1. Zero E1 row in this family skipped.

#### Canonical substrate (Read at source THIS session; cited by F-041..F-056)

- **`calculate_interest`@`interest_projection.py:49-114`** -- THE sole arithmetic
  apy_interest engine, Read in full. `DAYS_IN_YEAR = Decimal("365")` (`:44`) consumed ONLY in
  the daily branch (`:89`); the monthly branch uses `calendar.monthrange` actual month length
  (`:93-96`), quarterly uses actual quarter length (`:99-110`); terminal
  `quantize(Decimal("0.01"), ROUND_HALF_UP)` (`:114`). The 365 daily simplification is
  documented-accepted in the module docstring (`:7-34`, F-126/PA-06). `grep` THIS session
  (`calculate_interest|DAYS_IN_YEAR|366|monthrange` over `app/`) confirms the ONLY callers are
  `balance_calculator.calculate_balances_with_interest@:161` and
  `year_end_summary_service` (`_compute_interest_for_year@:1245` ->
  `calculate_balances_with_interest`; `_compute_pre_anchor_interest@:1864`) -- all delegate to
  the one engine; **zero independent interest re-derivation, zero `366`, single
  `DAYS_IN_YEAR`**.
- **`growth_engine`** -- `calculate_employer_contribution@:91-127` (sole employer producer,
  every return `quantize(TWO_PLACES, ROUND_HALF_UP)` `:114,:119-121,:123-125`);
  `project_balance@:164-294` (G1, growth `:243` HALF_UP, day-count `(1+r)**(days/365)-1`
  `:241`); `reverse_project_balance@:297-373` (G2, `:363` HALF_UP, same 365 `:355`). The
  engine is A-01-clean and uses the same 365 day-count basis as `interest_projection`
  (consistent, not 366).
- **`entry_service`** (orphans, Read `:348-446`): `compute_entry_sums@:348-368`
  (`(sum_debit, sum_credit)` partitioned by `entry.is_credit`);
  `build_entry_sums_dict@:371-402` (`total = debit + credit` `:399`; **no `remaining` key, no
  `estimated_amount` subtraction** `:392-402` -- Gate-A correction CONFIRMED at source: NOT an
  `entry_remaining` producer); `compute_remaining@:405-425`
  (`estimated_amount - sum(all e.amount)` `:424-425`; takes `estimated_amount` as a param,
  cannot switch on status -- Q-08); `compute_actual_from_entries@:428-446` (`sum(all e.amount)`
  `:446` -- arithmetically identical to `build_entry_sums_dict.total`).

---

### Finding F-041: apy_interest consistency (single engine; PA-06 cross-producer 365/366)

- Concept: `apy_interest`
- Symptom link: none developer-reported
- Paths compared (P2-d `02_concepts.md:2022-2030`):
  - A = per-period interest on `accounts/interest_detail.html` via
    `accounts.interest_detail@accounts.py:1299` (consumes `calculate_balances_with_interest`).
  - B = `savings/dashboard.html` (137,140) interest via `savings_dashboard_service`.
  - C = year-end aggregate `_compute_interest_for_year@year_end_summary_service.py:1207`
    **vs** the sum of per-period `calculate_interest@interest_projection.py:49` over the same
    year (PA-19 26-period invariant).
  - Pairs A-B, A-C, B-C.
- Path A/B: both route through `balance_calculator.calculate_balances_with_interest@:112-173`
  (Read at source): the per-period interest is `calculate_interest(balance=running_balance,
  apy, compounding, period_start, period_end)` `:161-167` -- the single engine. No re-derivation.
- Path C: `_compute_interest_for_year@:1207-1257` (Read at source) calls
  `calculate_balances_with_interest@:1245` and sums `interest_by_period.get(period.id)` for
  periods whose `start_date.year == year` (`:1253-1257`). The annual aggregate is **literally
  the sum of the per-period engine outputs** -> C-vs-per-period-sum is byte-identical BY
  CONSTRUCTION.
- Compared dimensions:
  - Effective-amount / status / scenario / is_deleted: the engine takes a `balance` scalar;
    `_compute_interest_for_year` builds it from a `Transaction` query filtered
    `scenario_id`, `is_deleted False`, period set (`:1234-1242`) -- identical filter to the
    `calculate_balances_with_interest` base it then re-walks. Not the divergence axis.
  - **Day-count (PA-06 cross-producer, the mandated verdict): CONSISTENT.** There is exactly
    ONE arithmetic interest producer (`calculate_interest`). `DAYS_IN_YEAR = Decimal("365")`
    is defined once (`interest_projection.py:44`) and used only in the daily branch (`:89`);
    `grep` THIS session over `app/` for `calculate_interest|DAYS_IN_YEAR|366|monthrange`
    confirms every other "producer" (`calculate_balances_with_interest@balance_calculator.py:161`,
    `_compute_interest_for_year@:1245`, `_compute_pre_anchor_interest@:1864`) DELEGATES to
    that one engine. **No producer uses 366; no independent re-derivation exists; all
    apy_interest producers make the SAME 365 daily assumption.** The internal
    daily(365)-vs-monthly/quarterly(actual-day-count via `calendar.monthrange@:94`,
    `:99-110`) inconsistency is WITHIN the single engine and is the documented-accepted
    simplification (module docstring `:7-34`, F-126/PA-06), NOT a cross-producer SILENT_DRIFT.
  - Quantization: single terminal `quantize(0.01, ROUND_HALF_UP)` `:114`. A-01-clean (the
    engine is not in the A-01 24-list; the Gate D A-01 caveat for the
    retirement/investment route layer is `growth`/F-042, not this engine).
  - ORM load-context: `calculate_balances_with_interest@:135` delegates the base balances to
    `calculate_balances` -- so the F-001/F-002 entries-load divergence propagates into the
    interest-inclusive series exactly as into the scalar balance (already recorded F-005); the
    interest LAYER itself is load-context-free (pure scalar `apy`/dates). Not re-verdicted here.
- Divergences: none in the interest arithmetic. A and B consume the identical engine; C is the
  per-period sum by construction (PA-19's "uncovered 26-period invariant" is a Phase-7
  test-gap, not a live drift -- the code already guarantees the equality by summing
  `interest_by_period`).
- Risk: none for `apy_interest` cross-producer consistency. The only known caveat is the
  documented-accepted leap-year 365 daily overstatement (~$1.23/$100K for a 14-day period
  crossing Feb 29, module docstring `:17-19`), which is UNIFORM across all producers (single
  engine) and therefore not a drift between pages.
- Verdict: **AGREE** -- single canonical engine `calculate_interest@interest_projection.py:49`
  Read in full; every consumer/aggregator delegates; the PA-06 365 simplification is uniform
  across ALL producers (no 366 anywhere; cross-producer CONSISTENT) and documented-accepted;
  the year-end aggregate equals the per-period sum by construction.
- If DIVERGE: n/a.
- Open questions for the developer: none. Cross-link **PA-06** (`00_priors.md:669`,
  documented-accepted, Phase 8 disposition note -- NOT a Phase-3 drift), **PA-17/PA-18/PA-19**
  (`00_priors.md:680-682`; HYSA exact-value / invalid-`compounding_frequency`
  (`interest_projection.py:111-112` `else: return ZERO`) / 26-period compounding -- all
  Phase-7 test-gaps), `apy_interest`'s appearance inside `year_summary_growth` YG2 (F-054,
  AGREE-by-construction), F-005 (`chart_balance_series` inherits the entries-load axis, not
  the interest axis).

---

### Finding F-042: growth consistency (consolidated; G1-G4 token split; PA-04/PA-05 body)

- Concept: `growth` (token-overloaded; E2 `02_concepts.md:3280` is the split spec)
- Symptom link: none developer-reported; PA-04/PA-05 are the open prior-audit findings.
- **Token-overload split (per E2, BEFORE any pairwise comparison -- comparing the unsplit
  token would be invalid):**
  - **G1** investment/account growth (money) -- `growth_engine.project_balance@:164-294`,
    growth `:243`.
  - **G2** reverse growth (money) -- `growth_engine.reverse_project_balance@:297-373`,
    `:363/:368`.
  - **G3** spending-trend percentage change (RATE, not money) --
    `spending_trend_service._safe_pct_change@:470-482` (`(last-first)/first*100`, HALF_UP
    `:482`), `_compute_trends@:97`.
  - **G4** year-end return/interest aggregates (money) -- `_compute_savings_progress@:887`
    (investment via `_project_investment_for_year`), `_compute_interest_for_year@:1207`,
    `_compute_mortgage_interest@:380`, `_compute_debt_progress@:824`.

#### Sub-concept verdict table

| Sub-concept | Producer(s) `file:line` (Read this session) | Pairwise verdict | Class | Cross-ref |
| --- | --- | --- | --- | --- |
| G1 (investment growth, money) | `growth_engine.project_balance@:243` (HALF_UP, 365 day-count `:241`) vs retirement-aggregation consumer `_project_retirement_accounts@retirement_dashboard_service.py:338` (delegates to the engine) | AGREE (consumer delegates to the single engine) | -- | F-054 YG1, F-055 |
| G2 (reverse growth, money) | `growth_engine.reverse_project_balance@:361-368` (HALF_UP `:363`) -- sole producer; consumed by year-end Jan-1 inference | AGREE (single producer) | -- | F-051/F-052 |
| G3 (spending-trend pct, RATE) | `_safe_pct_change@spending_trend_service.py:470-482` (HALF_UP `:482`) -- sole producer; **a percentage, not money** | AGREE (single producer); **MUST NOT be numerically compared with G1/G2/G4** | DEFINITION (token overload; mixed units) | E2 `02_concepts.md:3280` |
| G4 (year-end return/interest aggregates, money) | `_compute_savings_progress@:887` / `_project_investment_for_year@:1027` / `_compute_interest_for_year@:1207` / `_compute_mortgage_interest@:380` / `_compute_debt_progress@:824` | per-sub-concept owned by F-054 (YG1/YG2/YG3), F-053 | inherits F-054/F-053 | F-054, F-053 |
| **SWR/return slider input (PA-04/PA-05; the live DIVERGE this token carries)** | `compute_slider_defaults@retirement_dashboard_service.py:257-332` (display) vs `compute_gap_data@:217-221` (gap math) | **DIVERGE** | **SILENT_DRIFT** | escalated below |

The only escalated genuine DIVERGE sub-cluster is the PA-04/PA-05 SWR/return input (worked example
below); G1/G2 AGREE (single engine; the retirement/year-end sites delegate), G3 is a labeled
DEFINITION split (rate, not money), G4 is owned by F-053/F-054.

#### PA-04 body verification (mandated -- `compute_slider_defaults` Read in FULL `:257-332`)

The `compute_slider_defaults` docstring (`:265-301`) claims (1) "All arithmetic is performed in
Decimal" and (2) a stored SWR of exactly `Decimal("0")` is an explicit zero, only `None` triggers
the default. **Body reconciliation (Read at source):**

- Claim (1) Decimal: **CONFIRMED.** `:307-309` `current_swr = (settings.safe_withdrawal_rate
  - _PCT_SCALE).quantize(_PCT_QUANTUM)` and `:323-328``weighted_return / total_balance * _PCT_SCALE).quantize(_PCT_QUANTUM)`are all-Decimal
  (`_PCT_SCALE=Decimal("100")@:70`,`_PCT_QUANTUM=Decimal("0.01")@:76`); **no`float()`** in the body.
  PA-04's`float(... or 0.04)*100` defect IS remediated here.
- Claim (2) zero-SWR-explicit: **CONFIRMED for this function.** `:304` `if settings is None or
  settings.safe_withdrawal_rate is None:` -- uses `is None`, so a stored `Decimal("0")` SWR
  is honored as explicit zero (round-trips as `Decimal("0.00")`); only `None` -> `_DEFAULT_SWR_PCT`.
- **The docstring is accurate about its OWN body and does NOT overclaim** -- it never asserts
  PA-04 globally remediated. So priors 0.6's blanket "PA-04 open" (`00_priors.md:667`) is
  **PARTIALLY STALE** w.r.t. `compute_slider_defaults` claims (1)+(2) (a Phase-8 note: refine
  the priors row), **BUT PA-04 is NOT fully remediated** -- two live defects remain:
  1. **Cross-anchor SWR inconsistency (the live SILENT_DRIFT).** The SIBLING
     `compute_gap_data@:217-221` resolves the SWR that drives the actual gap math as
     `Decimal(str(settings.safe_withdrawal_rate or "0.04"))` -- a **truthiness `or`**, so a
     stored `Decimal("0")` SWR is silently replaced by `0.04`. `compute_slider_defaults`
     takes `data = compute_gap_data(...)` output (docstring `:277`), so BOTH run for the SAME
     retirement page render: for an explicit-zero SWR the **slider displays `0.00%`**
     (`:304-309`, correct `is None`) while the **gap projection and `chart_data`
     `investment_income` use `swr = 0.04`** (`:220`, `:240-241`). The displayed SWR and the
     SWR actually driving the projected income disagree, no error raised.
  2. **Zero-return-account exclusion (still live).** `:321` `if params and
     params.assumed_annual_return:` is a TRUTHINESS check -- a stored `assumed_annual_return
     == Decimal("0")` is falsy, so a zero-return account is skipped entirely: its balance is
     NOT added to `total_balance` and `0` is NOT added to `weighted_return`, so the
     balance-weighted average return is computed over a SMALLER denominator, overstating
     `current_return` whenever a zero-return account holds material balance (coding-standards
     "`0` and `None` mean different things" violation; this is PA-04's third sub-defect,
     never claimed remediated).
- **PA-05 reconciliation:** `_DEFAULT_SWR_PCT=Decimal("4.00")@:54`,
  `_DEFAULT_RETURN_PCT=Decimal("7.00")@:63`, `_PCT_SCALE@:70`, `_PCT_QUANTUM@:76` ARE now
  named constants with source-citing comments -> PA-05 remediated on the SLIDER path. BUT the
  fractional default in `compute_gap_data` is the raw literal `"0.04"` (`:220`, twice -- the
  `else Decimal("0.04")` and the `or "0.04"`), and `swr` again at `:240` -- an uncited magic
  number (no fractional `_DEFAULT_SWR` constant exists; `_DEFAULT_SWR_PCT` is the percentage
  form). **PA-05 PARTIALLY remediated** (slider path clean; gap path retains the `"0.04"`
  magic literal).
- Compared dimensions:
  - Source/definition: `compute_slider_defaults` SWR base = `settings.safe_withdrawal_rate`
    with `is None` semantics; `compute_gap_data` SWR base = same column with `or "0.04"`
    truthiness. Same stored column, two different zero-handling rules on one page.
  - Effective-amount/status/scenario/period: n/a (settings + projection inputs, no
    transaction filters).
  - ORM load-context: both read `data["settings"]` / `settings.safe_withdrawal_rate`
    (scalar column); `compute_slider_defaults` additionally lazy-queries `InvestmentParams`
    per projection `:317-320` (N+1, correct value) -- not the divergence axis.
  - Quantization: slider path A-01-clean (named Decimal constants, `:307-309,:323-328`); the
    gap path's `"0.04"` is a fractional magic literal (PA-05), not an A-01 rounding-mode
    issue. The Gate D A-01 caveat for `growth` applies to the
    `retirement_dashboard_service.py:197,211,214,240,390` + `investment.py:*` route-layer
    24-list sites (`02_concepts.md:3189`) -- recorded as a ROUNDING_DRIFT candidate at the
    aggregation/route layer; the `growth_engine` G1/G2 ENGINE is A-01-clean (Read at source).
- Divergences:
  - **SWR cross-anchor:** displayed `current_swr` (`compute_slider_defaults:304-309`, `is
    None`) vs gap-math `swr` (`compute_gap_data:220`, `or "0.04"`) for an explicit-zero SWR.
    SILENT.
  - **Zero-return-account exclusion:** `compute_slider_defaults:321` truthiness skips
    `assumed_annual_return == Decimal("0")` accounts -> overstated balance-weighted
    `current_return`. SILENT.
- Risk -- worked example (both PA-04 live sub-defects on one retirement page):
  - **SWR cross-anchor.** User explicitly sets `UserSettings.safe_withdrawal_rate =
    Decimal("0.0000")` (a deliberate "I will not draw down principal" stance; the column is
    `Numeric(5,4)` `CHECK 0..1`, so `0` is valid and meaningful). Page render:
    - Slider (`compute_slider_defaults:304`): `settings.safe_withdrawal_rate is None` is
      False -> `current_swr = (Decimal("0.0000") * 100).quantize(0.01) = Decimal("0.00")` ->
      the SWR slider shows **0.00%**.
    - Gap math (`compute_gap_data:220`): `Decimal(str(settings.safe_withdrawal_rate or
      "0.04"))` -- `Decimal("0.0000")` is FALSY -> `swr = Decimal("0.04")`. With
      `gap_result.projected_total_savings = $1,200,000`, `chart_data["investment_income"] =
      str((1200000 * 0.04 / 12).quantize(0.01)) = "$4,000.00"/mo` (`:240-241`) and the gap
      calc assumes a 4% draw. The user is shown a **0.00%** SWR slider but the projected
      retirement income and the income gap are computed at **4%** -- a $4,000/mo phantom
      income the displayed slider says is zero. No error.
  - **Zero-return exclusion.** Two retirement accounts, each `current_balance = $100,000`:
    account X `assumed_annual_return = Decimal("0.0000")` (a stable-value/cash sleeve),
    account Y `assumed_annual_return = Decimal("0.0700")`. True balance-weighted average
    return = `(100000*0 + 100000*0.07) / 200000 = 3.50%`. Code (`:321-328`): X is skipped
    (`Decimal("0")` falsy) -> `total_balance = $100,000` (Y only),
    `weighted_return = 100000 * 0.07 = 7000`, `current_return = (7000/100000 *
    100).quantize(0.01) = 7.00%`. The slider shows **7.00%** for a portfolio whose true
    balance-weighted return is **3.50%** -- the zero-return half-million is silently dropped
    from the denominator.
- Verdict: **DIVERGE** -- the SWR cross-anchor (`compute_slider_defaults:304` `is None` vs
  `compute_gap_data:220` `or "0.04"`) and the zero-return-account exclusion
  (`compute_slider_defaults:321` truthiness) are provable from code and hold independently of
  any pending Q. G1/G2 AGREE (single engine), G3 is a labeled DEFINITION split (rate vs
  money), G4 -> F-053/F-054.
- If DIVERGE: classification: **SILENT_DRIFT** (displayed SWR/return vs the value driving the
  gap math; no error, no label -- violates E-04, `00_priors.md:178-182`), with a Phase-8
  **priors-0.6-stale note** (PA-04 `compute_slider_defaults` Decimal+zero-SWR claims (1)+(2)
  ARE remediated; the residual live defects are the sibling `compute_gap_data:220` truthiness
  - the `:321` zero-return exclusion) and a **PA-05** magic-literal residue (`"0.04"`
  `:220,:240`). DEFINITION_DRIFT for the G1/G2/G3/G4 token overload (G3 rate vs money).
- Open questions for the developer: none new -- PA-04/PA-05 are resolved prior-audit findings
  (the code-vs-docstring reconciliation above is the mandated verification, not a "what is
  intended" ambiguity; the truthiness/zero-handling defects are findings by the
  coding-standards "`0` vs `None`" rule). Cross-link **PA-04** (`00_priors.md:667`,
  reclassify: `compute_slider_defaults` (1)+(2) remediated, sibling/`:321` live),
  **PA-05** (`00_priors.md:668`, slider remediated, gap `"0.04"` live), **PA-29**
  (`00_priors.md:692`; growth-engine directional-only tests -- Phase 7), **Q-15**
  (`09_open_questions.md:621-658`; G4/`year_summary_*` dispatcher -> F-051/F-052/F-054/F-055),
  Gate D A-01 caveat (`02_concepts.md:3189`; retirement/investment route-layer 24-list
  ROUNDING candidate). Remediation direction: make `compute_gap_data:220` use the same
  `is None` semantics (and a named fractional SWR constant) as `compute_slider_defaults:304`,
  and replace the `:321` truthiness with `is not None` so zero-return accounts keep their
  balance weight.

---

### Finding F-043: employer_contribution consistency

- Concept: `employer_contribution`
- Symptom link: none developer-reported
- Paths compared (P2-d `02_concepts.md:2154-2160`):
  - A = investment dashboard card `employer_contribution_per_period`@`investment.py:185-189`.
  - B = investment growth chart employer line: `growth_engine.project_balance@:265` employer
    call inside the projection loop.
  - C = annual `year_summary_employer_total` via `_project_investment_for_year@:1027` (F-055).
  - Pairs A-B, A-C (B-C is the projection-internal consistency).
- Canonical producer: `calculate_employer_contribution@growth_engine.py:91-127` -- the SOLE
  producer (Read at source; `match` branch `:116-125` =
  `min(employee_contribution, gross*cap_pct) * match_pct`, all HALF_UP). Both A and B delegate
  to it.
- Path A: `investment.py:185-189` Read at source -- `employer_contribution_per_period =
  growth_engine.calculate_employer_contribution(employer_params, periodic_contribution)` where
  `periodic_contribution = inputs.periodic_contribution` (`:183`, from
  `calculate_investment_inputs`, **NOT capped at the annual contribution limit**).
- Path B: `growth_engine.project_balance:258-267` Read at source -- inside the per-period
  loop: `if remaining_limit is not None: contribution = min(period_contrib_amount,
  remaining_limit)` (`:258-259`), `contribution = max(contribution, ZERO)` (`:262`), then
  `employer_contribution = calculate_employer_contribution(employer_params, contribution)`
  (`:265`) -- the employee contribution fed to the SAME canonical function is **capped at the
  remaining annual contribution limit**.
- Compared dimensions:
  - Producer: single canonical (`calculate_employer_contribution`); A and B both delegate.
    The divergence is the **`employee_contribution` argument**, not the function.
  - **Contribution-limit cap: DIVERGES.** A passes uncapped `periodic_contribution`
    (`investment.py:188`); B passes the limit-capped `contribution`
    (`growth_engine.py:259-265`). For a `match`-type employer on an account at/near its annual
    `annual_contribution_limit`, `calculate_employer_contribution`'s
    `min(employee_contribution, gross*cap_pct)` (`:122`) yields a LARGER match for the
    uncapped card (A) than for the capped chart line (B).
  - Quantization: the engine is A-01-clean (`:114,:119-125` HALF_UP); the Gate D A-01 caveat
    for `employer_contribution` is the `investment.py:*` route-layer 24-list sites
    (`02_concepts.md:3190`) -- `:187` itself delegates to the clean engine, so the caveat
    attaches to the other investment-route quantizes, recorded as a ROUNDING candidate, not
    this divergence.
  - ORM load-context: A's `periodic_contribution` from `calculate_investment_inputs`
    (deductions + averaged transfer contributions); B's per-period `period_contrib_amount`
    from the same inputs but then limit-capped in the loop. Same upstream, different cap
    treatment.
  - Status/scenario/is_deleted/period: not the axis (employer math is a pure function of the
    triple).
- Divergences: A (`investment.py:188`, uncapped) vs B (`growth_engine.py:259-265`,
  limit-capped) feed `calculate_employer_contribution` different `employee_contribution` for
  the same account-period when the annual limit binds -> the dashboard "Employer contribution
  per period" card overstates the per-period match relative to the growth chart's employer
  line (and relative to the year-end total, F-055, which sums the capped B-style value).
  SILENT.
- Risk -- worked example (match employer near the annual limit): `match`-type employer,
  `match_percentage = 1.00` (100% match), `match_cap_percentage = 0.06`,
  `gross_biweekly = $4,000.00`; employee `periodic_contribution = $1,000.00`/period;
  `annual_contribution_limit = $23,000`, and by the viewed period `ytd_contributions =
  $22,500` so `remaining_limit = $500`.
  - Path A (card, uncapped): `calculate_employer_contribution(employer_params, $1,000.00)`:
    `matchable_salary = (4000 * 0.06).quantize(.01,HALF_UP) = $240.00`;
    `matched = min(1000.00, 240.00) = $240.00`; employer = `(240.00 * 1.00).quantize = $240.00`.
    Card shows **$240.00**.
  - Path B (chart, capped): `contribution = min($1,000.00, remaining_limit $500.00) = $500.00`
    -> `calculate_employer_contribution(employer_params, $500.00)`: `matched = min(500.00,
    240.00) = $240.00`; employer = `$240.00`. (Equal here because `matchable_salary` <
    both.) Now take the LAST period where `remaining_limit = $100`: A still shows
    `min(1000,240)=240 -> $240.00`; B caps `contribution = min(1000,100)=$100`, `matched =
    min(100, 240) = $100.00`, employer = `(100.00*1.00) = $100.00`. **Card $240.00 vs chart
    $100.00 for the same period.** Over the limit-binding periods the dashboard card
    overstates the employer match (here by $140.00/period), and the year-end
    `year_summary_employer_total` (F-055, sums the capped B value) shows the lower figure --
    one account, three surfaces, no error.
- Verdict: **DIVERGE** (the uncapped-card vs limit-capped-chart divergence is provable from
  code: `investment.py:188` passes uncapped `periodic_contribution`, `growth_engine.py:259-265`
  passes the limit-capped `contribution`, both into the single canonical function).
- If DIVERGE: classification: **SILENT_DRIFT** (an unlabeled per-period employer figure that
  disagrees between the dashboard card, the growth chart, and the year-end total whenever the
  annual contribution limit binds; no error -- violates E-04). PLUS a Gate D **A-01
  ROUNDING** caveat at the `investment.py` route-layer 24-list aggregation sites
  (`02_concepts.md:3190`; recorded, not the primary divergence).
- Open questions for the developer: none new (the cap divergence is provable; whether the
  card SHOULD show capped or uncapped is a remediation choice, not a "which is the code's
  intent" ambiguity -- both behaviors exist and disagree, which is the finding). Cross-link
  **F-055** (`year_summary_employer_total` sums the capped B value -> inherits this DIVERGE),
  **F-042** G1 (the same `project_balance` loop), **PA-04/PA-05** (`00_priors.md:667-668`;
  employer/assumed-return magic-number fallbacks feed the same projection chain),
  **PA-29** (`00_priors.md:692`; growth-engine match/flat branches directional-only -- Phase 7),
  Gate D A-01 caveat (`02_concepts.md:3190`). Remediation direction: decide whether the
  dashboard card should display the limit-capped per-period employer match (consistent with
  the chart and year-end total) and, if so, pass the capped contribution to
  `calculate_employer_contribution` at `investment.py:187`.

---

### Finding F-044: contribution_limit_remaining consistency (single-path internal verify)

- Concept: `contribution_limit_remaining` (E1 single-path, `02_concepts.md:3262-3263`)
- Verdict: **AGREE.** Single-path by design (no service producer for the final figure; the
  `limit - ytd` subtraction is route-resident at `investment.py:173-181`, structurally like
  `transfer_amount_computed`/F-030). Read at source: `inputs =
  calculate_investment_inputs(...)` `:173-181`; `annual_contribution_limit` is the
  pass-through `getattr(investment_params, "annual_contribution_limit", None)`
  (`investment_projection.py:190`), `ytd_contributions` is `inputs.ytd_contributions`
  (`investment.py:190`, F-045). The route renders `limit`/`ytd`/remaining against the SAME
  `calculate_investment_inputs` output at `investment/dashboard.html:76` -- no recompute, no
  second producer. Inherits the F-045 `ytd_contributions` bypass (contract-safe) and the
  Gate D A-01 caveat for the route-resident quantize `investment.py:670`
  (`02_concepts.md:3191`; the `limit - ytd` subtraction itself is not a money `quantize`).
- Open questions for the developer: none. Cross-link **F-045**, **F-030**
  (`transfer_amount_computed`, same route-resident-derivation Phase-6 SRP class), Gate D A-01
  caveat (`02_concepts.md:3191`), Q-12 (route-resident aggregation SRP, A-12 proposed --
  cross-link only).

---

### Finding F-045: ytd_contributions consistency (single-path internal verify)

- Concept: `ytd_contributions` (E1 single-path, `02_concepts.md:3264-3265`)
- Verdict: **AGREE.** Sole producer `calculate_investment_inputs@investment_projection.py:100`
  Step 4 (`:175-187`, Read at source): `ytd_contributions += Decimal(str(t.estimated_amount))`
  for shadow contributions whose `pay_period_id` is in the YTD set, **gated `and not
  t.status.excludes_from_balance`** (`:186`). The `estimated_amount` direct read is the
  contract-safe bypass recorded as F-027 row **S18** (tier-2 honored inline at `:150-151`
  active-filter and `:186` ytd-filter; tier-1/3/4 immaterial for transfer shadows per
  Invariant 3). Single producer; the route consumes `inputs.ytd_contributions`
  (`investment.py:190`) unchanged -> no recompute drift. The feeder
  `periodic_contribution` passes through `:93,:96,:159` `.quantize(TWO_PLACES)` with no
  `rounding=` -> banker's default (the A-01 24-list `investment_projection.py:93,96,159`
  sites, `09_open_questions.md:42`) -- a Gate D **A-01 ROUNDING** caveat on the contribution
  average, NOT on the `ytd_contributions` sum itself (which is a raw `+=`, A-01-acceptable
  raw-sum-into-display per inventory 1.7.6); recorded as a ROUNDING candidate for Phase
  6/8, the `ytd_contributions` producer itself is single-path AGREE.
- Open questions for the developer: none. Cross-link **F-027 S18** (the `effective_amount`
  bypass row, EQUIVALENT/contract-safe), **F-044**, Gate D A-01 caveat
  (`02_concepts.md:3191`).

---

### Finding F-046: goal_progress consistency (consolidated; GP1-GP2 token split)

- Concept: `goal_progress` (token-overloaded; E2 `02_concepts.md:3282` is the split spec)
- Symptom link: none developer-reported (GP2 shares the Q-08 "which amount" family as #1).
- **Token-overload split (per E2, BEFORE comparison):**
  - **GP1** savings-goal completion (money + ETA) -- `savings_goal_service`
    (`resolve_goal_target@:21`, `calculate_required_contribution@:109`,
    `calculate_trajectory@:331`, `_compute_required_monthly@:431`).
  - **GP2** entry-tracked spend progress (PERCENTAGE) --
    `dashboard_service._entry_progress_fields@:203` (`entry_over_budget` `:245`) and
    `companion._build_entry_data@:53-56` inline `pct = float(total/estimated*100)`.

#### Sub-concept verdict table

| Sub-concept | Producer(s) `file:line` (Read this session) | Pairwise verdict | Class | Cross-ref |
| --- | --- | --- | --- | --- |
| GP1 target | `resolve_goal_target@savings_goal_service.py:21-106` (final `quantize(_TWO_PLACES, HALF_UP)` `:106`; 26/12 via named `_PAY_PERIODS_PER_YEAR/_MONTHS_PER_YEAR` `:17-18`) | AGREE (single producer) | -- | F-047 (same 26/12 constants) |
| GP1 required-contribution | `calculate_required_contribution@:109-136` (`(gap/remaining_periods).quantize(0.01, HALF_UP)` `:134-135`) | AGREE (single producer; A-01-clean) | -- | -- |
| GP1 trajectory/ETA | `calculate_trajectory@:331-405` (`months = int((remaining/monthly).to_integral_value(ROUND_CEILING))` `:391-395`); `_compute_required_monthly@:431-464` (`(remaining/months_available).quantize(_TWO_PLACES, ROUND_CEILING)` `:462-463`) | AGREE (single producer; ROUND_CEILING **documented-INTENTIONAL** per docstring `:438` "so the user contributes at least enough", applied at the SINGLE site, called only `:387,:404` -- Gate D-confirmed exception, NOT an A-01 violation) | -- | Gate D `02_concepts.md:3194`; A-01 verification `09_open_questions.md:49-51` |
| GP1 multi-page render (/savings, dashboard widget, retirement-gap) | consumers delegate to the above `savings_goal_service` producers (P2-d primary-path `02_concepts.md:2452-2459`) | AGREE (consumers delegate; no recompute) | -- | -- |
| GP2 (entry-tracked pct) | `_entry_progress_fields@dashboard_service.py:237-245` (`total=debit+credit`, `entry_over_budget = total > estimated`) vs `_build_entry_data@companion.py:50-56` (`pct = float(total/estimated*100)`) -- both delegate `compute_entry_sums`/`compute_remaining` to `entry_service` | **UNKNOWN** (estimated-vs-actual base) | SILENT (Q-08) + E-10 float `companion.py:54` | **F-028** (Q-08 cluster, already recorded -- not re-derived); F-056 `entry_remaining` |
| GP1 vs GP2 | money+ETA vs percentage(float) | **NOT numerically comparable** | DEFINITION (token overload, mixed units) | E2 `02_concepts.md:3282` |

- Compared dimensions (GP2, the only non-AGREE sub-cluster):
  - Effective-amount logic: both GP2 impls anchor on raw `txn.estimated_amount`
    (`dashboard_service.py:245`, `companion.py:54`) via `compute_remaining`/inline, never
    tier-3 `actual_amount` even for a settled entry-tracked txn -- the F-028 / Q-08 axis,
    **already recorded; not re-derived here.** The two GP2 impls AGREE WITH EACH OTHER
    (identical `compute_entry_sums` total, identical estimated base).
  - Quantization: GP1 A-01-clean except the documented-intentional `ROUND_CEILING@:462-463`
    (verified single-site, consistent). GP2 `pct` is `float(...)` (`companion.py:54`) -- an
    E-10 float-on-money display concern (recorded, not the numeric driver; `_entry_progress_fields`
    has no pct field, it emits the `entry_over_budget` bool).
  - ORM load-context: both GP2 impls require `txn.entries` loaded (caller eager-loads);
    not a value-divergence axis (lazy-load yields the same value).
- Divergences: GP1 none (single canonical owner per sub-function; ROUND_CEILING
  documented-intentional, applied consistently). GP2: estimated-vs-actual base is the F-028
  Q-08 SILENT cluster (cross-ref, not re-derived); the two GP2 impls agree with each other.
- Risk: GP1 none. GP2 -- the F-028 worked example governs (entry-tracked DONE txn,
  `estimated $120`, `actual $100`, entries `$80`: `pct = float(100/120*100) = 83.33%` on the
  estimated base; if interp (2) "what you spent" is intended it should anchor on
  `actual_amount`). Not re-derived (F-028 owns it).
- Verdict: **GP1 AGREE** (single canonical `savings_goal_service` producers Read at source;
  ROUND_CEILING documented-intentional and applied consistently at the single
  `_compute_required_monthly:462-463` site). **GP2 UNKNOWN** for the estimated-vs-actual base
  -- blocked on **Q-08** (`09_open_questions.md:288-323`, A-08 proposed; the cross-anchor
  inconsistency is recorded SILENT in **F-028** independent of Q-08). **GP1 vs GP2 DEFINITION
  (token overload -- not numerically comparable, E2-mandated split).**
- If DIVERGE (GP2 conditional on Q-08): SILENT_DRIFT (cross-ref F-028). DEFINITION_DRIFT for
  the GP1/GP2 token overload (money+ETA vs float percentage).
- Open questions for the developer: **Q-08** (governing GP2; A-08 proposed). No new question
  (Q-08 already frames it; GP1 is unambiguous). Cross-link **F-028** (the escalated Q-08
  cluster -- GP2 is one of its sites, recorded there), **F-056** (`entry_remaining` shares
  the same `compute_remaining`/estimated base), **F-027** rows R1/R2 (`companion.py:52,54-55`),
  E2 `02_concepts.md:3282`.

---

### Finding F-047: emergency_fund_coverage_months consistency (single-path internal verify)

- Concept: `emergency_fund_coverage_months` (E1 single-path, `02_concepts.md:3266-3267`)
- Verdict: **AGREE.** Sole producer `calculate_savings_metrics@savings_goal_service.py:139-175`
  (Read at source): `months = (savings_balance/avg_expenses).quantize(Decimal("0.1"),
  ROUND_HALF_UP)` `:163-164`; the two derived figures are internally consistent against the
  SAME `months` -- `paychecks_covered = (months * Decimal("26")/Decimal("12")).quantize(0.1,
  HALF_UP)` `:169-170`, `years_covered = (months/Decimal("12")).quantize(0.1, HALF_UP)`
  `:172-173`; zero/None expenses -> zeros `:155-160`. The `0.1` precision is a
  coverage-DURATION ratio, **not a money value** -> A-01 (money-scoped per the A-01
  verification verdict, `09_open_questions.md:37-62`, and Gate D `02_concepts.md:3175`) does
  NOT apply; not a violation. The 26/12 factor uses the named module constants
  `_PAY_PERIODS_PER_YEAR`/`_MONTHS_PER_YEAR` (`:17-18`); the duplicate 26/12 inline forms at
  `savings_dashboard_service.py:170-172,765` are the Q-12 DRY cross-link (A-12 proposed,
  pending -- cross-link only, NOT this finding's verdict). Single producer; the route
  consumes the dict unchanged (`savings.py:107` -> `savings/dashboard.html:298,304,310`) ->
  no recompute drift. Distinct from `cash_runway_days` by design (different balance base,
  lookback, unit -- F-048).
- Open questions for the developer: none. Cross-link **F-048** (sibling, NOT folded -- P2-d
  resolved), **Q-12** (`09_open_questions.md:459-512`; 26/12 duplication, A-12 proposed --
  cross-link only), **F-046** GP1 (shares the `savings_goal_service` 26/12 constants).

---

### Finding F-048: cash_runway_days consistency (single-path internal verify)

- Concept: `cash_runway_days` (E1 single-path, `02_concepts.md:3267-3268`)
- Verdict: **AGREE.** Sole producer `_compute_cash_runway@dashboard_service.py:375-417`
  (Read at source): returns `0` if balance <= 0 `:387-388`, `None` if no trailing-30-day
  settled spending `:412-413` (avoids infinity), else `int((current_balance /
  daily_avg).quantize(Decimal("1"), ROUND_HALF_UP))` `:415-417`. Uses `txn.effective_amount`
  CORRECTLY (`:411`, the property -- NOT a bypass; F-027 confirms it is not in the bypass
  table). The mandated single-path verify: the `current_balance` input must equal the same
  period's `checking_balance` the dashboard shows. **Verified at source:**
  `_get_balance_info@dashboard_service.py:348-351` sets `current_balance =
  balance_results.get(current_period.id, account.current_anchor_balance or _ZERO)` where
  `balance_results = _compute_balances(account, all_periods, scenario)` (`dashboard_service.py:73`,
  the `calculate_balances` path, entries loaded `:689` per F-001) and passes it to
  `_compute_cash_runway` at `:363`. So the runway's balance base IS byte-identical to the
  dashboard `checking_balance` card for the same `(account, current_period)` -> no
  independent balance recompute. It inherits the F-001/F-002 entries-load axis ONLY as the
  shared upstream balance (cross-ref, NOT a new drift in `cash_runway_days` itself; if the
  dashboard balance is the entry-aware figure the runway uses the SAME figure -- consistent
  by construction with the card beside it).
- Open questions for the developer: none. Cross-link **F-001/F-002** (the shared upstream
  `_compute_balances` entries-load axis -- inherited, not re-verdicted), **F-047** (sibling,
  not folded). The `days` unit / `Decimal("1")` quantize is not money (A-01 n/a).

---

### Finding F-049: pension_benefit_annual consistency (single-path internal verify)

- Concept: `pension_benefit_annual` (E1 single-path, `02_concepts.md:3269-3271`)
- Verdict: **AGREE.** Sole producer `calculate_benefit@pension_calculator.py:31-75`
  (Read at source): `annual_benefit = (benefit_multiplier * years_of_service *
  high_avg).quantize(TWO_PLACES, ROUND_HALF_UP)` `:61-63` -- A-01-clean; `0` when no salary
  history `:49-55`. The mandated feed-consistency verify: `years_of_service` and the
  high-salary average derive from `project_salaries_by_year@:78-111`, which at `:94,:109`
  calls `paycheck_calculator._apply_raises` -- i.e. the pension salary projection uses the
  SAME raise engine as `paycheck_gross` (cross-ref **F-032** P3-d1), not a re-implementation
  (the `:81` "simplified" caveat is only the annual `_FakePeriod(date(year,12,1))` sampling,
  by design for an annual pension benefit). `_calculate_years_of_service@:114-123` uses
  `delta_days / Decimal("365.25")` -- a years-of-SERVICE duration (leap-year-aware), NOT an
  interest/growth rate, so it is NOT the PA-06 365/366 axis (different concept, by design).
  Consumers (`compute_gap_data@retirement_dashboard_service.py:79`,
  `calculate_gap@retirement_gap_calculator.py:37`) are pass-throughs (1.7.8) -- no recompute.
- Open questions for the developer: none. Cross-link **F-032** (shared `_apply_raises` raise
  engine -- a raise-omission divergence there would propagate into pension salaries, but the
  pension path itself correctly delegates), **F-050** (the monthly facet), **PA-30**
  (`00_priors.md:693`; pension directional-only tests -- Phase 7 test-gap).

---

### Finding F-050: pension_benefit_monthly consistency (single-path internal verify)

- Concept: `pension_benefit_monthly` (E1 single-path, `02_concepts.md:3269-3271`)
- Verdict: **AGREE.** Same sole producer `calculate_benefit@pension_calculator.py:31`:
  `monthly_benefit = (annual_benefit / 12).quantize(TWO_PLACES, ROUND_HALF_UP)` `:65-67`
  (Read at source) -- the documented `annual/12` relationship, A-01-clean, computed in the
  SAME call as `pension_benefit_annual` (F-049) so the two are consistent by construction
  (`monthly == annual/12` exactly, modulo the single HALF_UP). Consumers
  (`compute_gap_data@retirement_dashboard_service.py:79`,
  `calculate_gap@retirement_gap_calculator.py:37`, `retirement/dashboard.html:118`,
  `_gap_analysis.html:15,22`, `retirement_gap_chart.js` data-attr display-only/E-17) are
  pass-throughs threading `monthly_pension_income` -- no recompute (verified P2-d
  `02_concepts.md:2586-2598`).
- Open questions for the developer: none. Cross-link **F-049** (computed in the same call;
  `monthly = annual/12`), **PA-30** (`00_priors.md:693`; Phase 7 test-gap).

---

### Finding F-051: year_summary_jan1_balance consistency (Q-15-gated; cross-ref F-006)

- Concept: `year_summary_jan1_balance`
- Symptom link: #5 (inherits `net_worth`)
- Paths compared: single token producer `_compute_net_worth@year_end_summary_service.py:689` (the
  `jan1` endpoint; `delta = dec31 - jan1` `:746`) -- the SAME producer and the SAME per-account
  dispatch (`_get_account_balance_map`/`_build_account_data@:750`) as P2-a `net_worth`. The
  comparison is on its INPUTS: the net-worth dispatch (`_get_account_balance_map@:2036`) vs the
  parallel `_compute_account_projections@savings_dashboard_service.py:294` that drives
  `/savings`/dashboard for the same account's Jan-1 balance (W-152/W-159).
- Verdict: **UNKNOWN** -- blocked on **Q-15** (`09_open_questions.md:621-658`; which
  per-account dispatcher is canonical; whether net_worth_amort W-152/W-159 is "code must
  catch up" PLAN_DRIFT or "plan superseded"). This is the SAME producer/dispatcher question
  P3-a F-006 (`net_worth`) recorded; **cross-referenced, NOT re-derived** (the prompt: do not
  re-derive the P3-a aggregate findings). The concrete divergences (loan-base
  schedule-vs-engine, anchor-None, dual dispatch, W-159 investment Dec-31 equality) are
  enumerated in F-006 and hold regardless; only the verdict label is gated because the
  plan-vs-code direction is the developer's call (audit plan section 9).
- If DIVERGE (conditional on Q-15): PLAN_DRIFT (dual dispatch, W-152) / SOURCE_DRIFT
  (loan base) / SCOPE_DRIFT (anchor-None) -- per F-006.
- Open questions for the developer: **Q-15** (governing; cross-ref F-006). Cross-link
  **F-006** (`net_worth`, same producer -- the authoritative analysis), **F-052** (the Dec-31
  endpoint of the same `_compute_net_worth` trend), **PA-26** (`00_priors.md:689`;
  net-worth-trend chart value-verification -- Phase 7 test-gap).

---

### Finding F-052: year_summary_dec31_balance consistency (Q-15-gated; cross-ref F-006/F-007)

- Concept: `year_summary_dec31_balance`
- Symptom link: #5
- Paths compared: same sole producer `_compute_net_worth@year_end_summary_service.py:689`
  (the `dec31` endpoint, identical dispatch to F-051). The W-159 sub-pair: Dec-31 net-worth
  investment balance via `_get_account_balance_map@:750` (inside `_compute_net_worth:689`)
  **vs** `_project_investment_for_year` (savings-progress, via `_compute_savings_progress@:887`)
  for the same account -- net_worth_amort W-159 requires these EQUAL and there is no code
  enforcing it (recorded in P3-a F-006 divergence bullets and F-007).
- Verdict: **UNKNOWN** -- blocked on **Q-15** (same dispatcher question as F-006/F-007;
  cross-referenced, NOT re-derived). The W-159 Dec-31 investment-equality gap is the F-006
  "Investment Dec-31" / F-007 W-159 divergence, recorded there; this concept inherits it.
- If DIVERGE (conditional on Q-15): PLAN_DRIFT (W-159 investment equality, dual dispatch) /
  SOURCE_DRIFT (loan base) / SCOPE_DRIFT (anchor-None) -- per F-006/F-007.
- Open questions for the developer: **Q-15** (governing; cross-ref F-006/F-007). Cross-link
  **F-006** (`net_worth` -- authoritative), **F-007** (`savings_total`; the W-159
  savings-progress-vs-net-worth investment Dec-31 equality), **F-054** YG1 (the
  `_project_investment_for_year` side of W-159), **PA-26** (`00_priors.md:689`; Phase 7).

---

### Finding F-053: year_summary_principal_paid consistency (A-06; cross-ref F-017)

- Concept: `year_summary_principal_paid`
- Symptom link: #3 (per-period principal reduction is E-03's mechanism; inherits #2/#4 via
  the schedule's `monthly_payment` inputs)
- Paths compared: A = `_compute_debt_progress@year_end_summary_service.py:824-882`
  (`principal_paid = jan1_bal - dec31_bal` `:871`, jan1/dec31 via
  `_balance_from_schedule_at_date` over `debt_schedules`) **vs** B = the loan dashboard's
  `principal_paid_per_period` from `generate_schedule@amortization_engine.py:326` summed over
  the same year (P2-b `principal_paid_per_period`, F-017). Pair A-B.
- Path A (Read at source `:824-882`): `schedule = debt_schedules.get(account.id)` `:851`;
  `jan1_bal = _balance_from_schedule_at_date(schedule, date(year-1,12,31), original)` `:865-867`;
  `dec31_bal = ...(schedule, date(year,12,31), original)` `:868-870`;
  `principal_paid = jan1_bal - dec31_bal` `:871` -- a raw subtraction of two `Numeric(12,2)`
  schedule balances (not re-quantized; operands already 2dp). `debt_schedules` is
  `_generate_debt_schedules` (the A-06-preprocessed `load_loan_context` source per F-017/F-018).
- Compared dimensions:
  - Source: A is the Dec-31(prior-year)->Dec-31(year) delta of the A-06-prepared schedule;
    B is the sum of that SAME schedule's per-row `principal` over the year. Per F-017's A-vs-C
    analysis (P3-b): `_compute_debt_progress`'s jan1-dec31 delta MUST equal the per-row
    principal sum for the year, and BOTH consume `load_loan_context` (A-06-prepared) ->
    **AGREE BY CONSTRUCTION** (F-017 already recorded the A-C pair AGREE by construction; this
    concept IS that pair from the year-end side).
  - A-06 / ORM load-context: A's schedule = `_generate_debt_schedules` (A-06-prepared, ARM
    anchor); the loan dashboard's per-period principal = `get_loan_projection` via the SAME
    `load_loan_context` -> consistent. The raw-caller A-06 bypass
    (`savings_dashboard_service.py:471,488`, `debt_strategy.py:175,181`) is the F-017/F-018
    DEFINITION/SCOPE divergence, NOT this aggregate (it does not feed `_compute_debt_progress`).
  - Quantization: raw subtraction of pre-quantized schedule balances; A-01 n/a. Inherits the
    F-013 SILENT `monthly_payment` input substrate (sites 1-8) only insofar as the schedule
    itself does.
- Divergences: none NEW. A-vs-B AGREE by construction (F-017). The concept inherits, not
  re-derives, F-013's SILENT `monthly_payment` input drift and F-018's A-06 raw-caller
  DEFINITION -- it is only as stable as the underlying A-06-prepared schedule.
- Verdict: **AGREE-by-construction** with the per-period schedule sum (cross-ref **F-017**:
  the year-end `jan1-dec31` delta and the per-period principal sum read the SAME
  `load_loan_context` A-06-prepared schedule). Inherits (cross-ref, not re-verdicted) the
  F-013/A-05 `monthly_payment` input SILENT substrate and the F-018/A-06 raw-caller DEFINITION.
- If DIVERGE: n/a for the A-B pair (AGREE by construction); the inherited substrate carries
  its own classification in F-013/F-017/F-018.
- Open questions for the developer: none new. Cross-link **F-017** (`principal_paid_per_period`
  -- the authoritative A-C-by-construction analysis), **F-018** (A-06 sibling
  `_compute_mortgage_interest`, the same `_generate_debt_schedules` source), **F-013/A-05**
  (the `monthly_payment` substrate the schedule rests on), **A-06**
  (`09_open_questions.md:223-247`), **PA-12/PA-27** (`00_priors.md:675,690`; debt/amortization
  directional-only -- Phase 7 test-gap).

---

### Finding F-054: year_summary_growth consistency (consolidated; YG1-YG3 token split)

- Concept: `year_summary_growth` (token-overloaded; E2 `02_concepts.md:3281` is the split
  spec; one token, three quantities, NOT summable)
- Symptom link: #5 (YG1 inherits `net_worth`)
- **Token-overload split (per E2, BEFORE comparison):**
  - **YG1** investment growth/return for the year -- `_compute_savings_progress@:887` ->
    `_project_investment_for_year@:1027` `growth_total`.
  - **YG2** interest-account growth for the year -- `_compute_interest_for_year@:1207`
    (+ `_compute_pre_anchor_interest@:1820`).
  - **YG3** mortgage interest paid for the year -- `_compute_mortgage_interest@:380`.

#### Sub-concept verdict table

| Sub-concept | Producer `file:line` (Read this session) | Pair compared | Verdict | Class | Cross-ref |
| --- | --- | --- | --- | --- | --- |
| YG1 (investment growth) | `_project_investment_for_year@year_end_summary_service.py:1027-1126` (via `_compute_savings_progress:936-942`); base balance from `_get_account_balance_map@:1064` (the Q-15 net-worth-side dispatcher) | YG1 annual vs `growth_engine.project_balance@:164` per-period growth sum over the year (W-151/W-159) | **UNKNOWN** | PLAN/SOURCE (Q-15 dispatcher) | **F-006** (`net_worth` dispatcher), F-042 G4, F-052 (W-159 Dec-31), F-055 |
| YG2 (interest-account growth) | `_compute_interest_for_year@:1207-1257` (sums `interest_by_period` from `calculate_balances_with_interest@:1245`) + `_compute_pre_anchor_interest@:1820` | YG2 annual vs sum of per-period `calculate_interest@interest_projection.py:49` | **AGREE-by-construction** (YG2 literally sums the per-period engine output `:1253-1257`) | -- | **F-041** (`apy_interest` single engine); PA-19 Phase-7 |
| YG3 (mortgage interest paid) | `_compute_mortgage_interest@:380-408` (`total_interest += row.interest` for `row.payment_date.year==year` over `debt_schedules`) | YG3 vs `generate_schedule@amortization_engine.py:326` per-row interest (A-06) | **AGREE-by-construction** (same A-06 `_generate_debt_schedules`; = F-018 Path B, A-B AGREE by construction) | -- | **F-018** (`interest_paid_per_period` A-06; authoritative) |
| YG1 vs YG2 vs YG3 | investment return vs interest accrual vs debt interest paid | NOT summable into one "growth" | DEFINITION (token overload) | DEFINITION | E2 `02_concepts.md:3281` |

- Compared dimensions (the only gated sub-concept, YG1):
  - Source: YG1's base balance comes from `_get_account_balance_map@:1064` -- the SAME
    net-worth-side dispatcher F-006 flagged for Q-15 (vs `_compute_account_projections`); the
    growth math then runs `growth_engine` (A-01-clean engine, F-042 G1). The dispatcher
    canonicality is Q-15; the engine is not the divergence.
  - YG2/YG3: AGREE by construction -- YG2 sums the single apy_interest engine's per-period
    output (`:1253-1257`, cross-ref F-041); YG3 sums the A-06 `_generate_debt_schedules` rows
    (= F-018 Path B, A-B AGREE by construction). Neither is a new drift.
  - Quantization: the engines (growth_engine, interest_projection, amortization_engine) are
    A-01-clean; the year-end sums are raw aggregates of already-2dp values. The Gate D A-01
    caveat for `growth`'s aggregation/route layer (`02_concepts.md:3189`) attaches to the
    retirement/investment ROUTE sites, recorded as a ROUNDING candidate (cross-ref F-042),
    not to YG1/YG2/YG3 producers.
- Divergences: YG1 is the Q-15 dispatcher question (cross-ref F-006, not re-derived). YG2/YG3
  AGREE by construction. The YG1/YG2/YG3 token overload is a DEFINITION split (the three must
  not be summed into one "growth" without separation -- E2-mandated).
- Verdict: **YG1 UNKNOWN** (blocked on **Q-15**; cross-ref F-006/F-042 G4 -- not re-derived).
  **YG2 AGREE-by-construction** (cross-ref F-041; `_compute_interest_for_year` sums the
  single apy_interest engine output). **YG3 AGREE-by-construction** (cross-ref F-018; same
  A-06 schedule as the loan dashboard). **YG1/YG2/YG3 DEFINITION** (token overload, not
  summable -- E2 split).
- If DIVERGE (YG1 conditional on Q-15): PLAN_DRIFT/SOURCE_DRIFT per F-006. DEFINITION_DRIFT
  for the token overload.
- Open questions for the developer: **Q-15** (governing YG1; cross-ref F-006). No new
  question. Cross-link **F-006** (`net_worth` dispatcher -- authoritative), **F-041**
  (`apy_interest`, YG2 engine), **F-018** (`interest_paid_per_period`, YG3 A-06),
  **F-042** G4 (the `growth` token's year-end sub-concept), **F-052** (W-159 Dec-31),
  **A-06** (`09_open_questions.md:223-247`), **PA-06/PA-17/PA-19** (`00_priors.md:669,680,682`;
  YG2 interest precision -- Phase 7), **PA-29** (`00_priors.md:692`; YG1 growth-engine
  directional-only -- Phase 7).

---

### Finding F-055: year_summary_employer_total consistency (inherits F-043; Q-15-gated)

- Concept: `year_summary_employer_total`
- Symptom link: #5 (inherits the `net_worth`/savings-progress dispatcher)
- Paths compared: the annual figure from `_compute_savings_progress@:887` via
  `_project_investment_for_year@:1027` (`employer_total`) **vs** the sum over the year of
  per-period `growth_engine.calculate_employer_contribution@:91` (P2-d
  `02_concepts.md:2756-2761`). Pair: annual-aggregate vs per-period-sum; plus the F-043
  card-vs-chart cross-ref.
- Path (Read at source): `_compute_savings_progress:936-942` -> `_project_investment_for_year@:1027`
  returns `employer_total` (the 3rd tuple element); `_project_investment_for_year` runs the
  growth-engine projection whose employer line is `growth_engine.project_balance:265`
  (`calculate_employer_contribution(employer_params, contribution)` where `contribution` is
  **limit-capped** at `:259-262`). So the year-end employer total is the **sum of the
  limit-CAPPED per-period employer match** -- consistent BY CONSTRUCTION with the growth
  chart's employer line (F-043 Path B) but **DIVERGENT from the investment dashboard CARD**
  (F-043 Path A, uncapped `investment.py:188`). The base balance dispatch is
  `_get_account_balance_map@:1064` (the Q-15 net-worth-side dispatcher).
- Compared dimensions:
  - Producer: the per-period engine is the single canonical
    `calculate_employer_contribution@growth_engine.py:91`; the annual total delegates to it
    via the projection loop. The annual-vs-per-period-sum pair AGREES by construction (the
    annual figure IS the per-period sum within `_project_investment_for_year`).
  - **Cap axis: inherits F-043.** The year-end total uses the limit-CAPPED per-period
    employer match (`growth_engine.py:259-265`); the dashboard CARD
    (`investment.py:185-189`) uses the UNCAPPED match. For a match-type employer near the
    annual contribution limit the dashboard card overstates the per-period employer match
    relative to both the growth chart AND this year-end total -> the year-end
    `year_summary_employer_total` and the dashboard card disagree for the same account/year.
    SILENT (F-043, not re-derived -- this concept is the year-end consumer of F-043 Path B).
  - Dispatcher: the base balance / per-account-type dispatch is the Q-15 question
    (`_get_account_balance_map` vs `_compute_account_projections`), cross-ref F-006 -- the
    dispatcher canonicality governs whether this aggregate is computed over the same account
    set as `/savings`.
  - Quantization: per-period engine A-01-clean (`growth_engine.py:114,:119-125` HALF_UP);
    annual = raw sum of 2dp values. Gate D A-01 caveat at the retirement/investment route
    aggregation 24-list (`02_concepts.md:3190,3257`) -- recorded as a ROUNDING candidate
    (cross-ref F-043), not the primary divergence.
- Divergences: (1) inherits F-043 -- the annual total (capped, = chart) vs the dashboard card
  (uncapped) disagree for a match employer near the limit; (2) the dispatcher axis is Q-15
  (cross-ref F-006). The annual-vs-per-period-sum pair itself AGREES by construction.
- Risk: same worked example as **F-043** (match employer, last limit-binding period: card
  $240.00 vs chart/year-end-total $100.00) -- not re-derived; the year-end total is F-043's
  Path B summed over the year, so it carries the lower (capped) figure while the dashboard
  card carries the higher (uncapped) one for the same account/year.
- Verdict: **DIVERGE** for the card-vs-(chart/year-end-total) axis -- inherits **F-043**
  (provable from code: `investment.py:188` uncapped vs `growth_engine.py:259-265` capped, the
  year-end total summing the capped value). The per-account dispatcher axis is **UNKNOWN,
  blocked on Q-15** (cross-ref F-006, not re-derived).
- If DIVERGE: classification: **SILENT_DRIFT** (inherits F-043 cap divergence) + PLAN/SOURCE
  (Q-15 dispatcher, per F-006) + Gate D A-01 ROUNDING caveat at the aggregation/route layer.
- Open questions for the developer: **Q-15** (governing the dispatcher; cross-ref F-006).
  No new question (the cap divergence is F-043's, recorded there). Cross-link **F-043**
  (`employer_contribution` -- the authoritative card-vs-chart cap analysis; this concept is
  its year-end consumer), **F-006** (`net_worth` dispatcher), **F-042** G4 / **F-054** YG1
  (the co-located `_project_investment_for_year` outputs), **PA-04/PA-05**
  (`00_priors.md:667-668`; employer/assumed-return fallbacks feed
  `_project_investment_for_year`), **PA-29** (`00_priors.md:692`; Phase 7).

---

### Finding F-056: entry_sum_total + entry_remaining (the two Gate-A orphans, consolidated)

- Concept: `entry_sum_total` and `entry_remaining` (the two Gate-A orphans,
  `02_concepts.md:2897-3018`; one section-3 finding covering both, as the session mandate
  permits). Q-08-governed; cross-ref **F-028** (the Q-08 entry-progress cluster, already
  recorded in P3-c -- NOT re-derived here; this finding adds only what is NEW for these two
  tokens).
- Symptom link: none developer-reported (same "which amount is read" family as #1 via Q-08).
- **Gate-A correction respected and re-verified at source THIS session:**
  `build_entry_sums_dict@entry_service.py:392-402` returns `{"debit","credit","total","count"}`
  -- **no `remaining` key, no `estimated_amount` subtraction** -> it is NOT an
  `entry_remaining` producer (it produces `entry_sum_total`). The sole `entry_remaining`
  producer is `compute_remaining@entry_service.py:405-425`.

#### entry_sum_total

- Paths compared (P2-d/E1 `02_concepts.md:3251`):
  - A = Python `compute_entry_sums@entry_service.py:348-368` / `build_entry_sums_dict@:371-402`
    (`total = debit + credit` `:399`).
  - B = `compute_actual_from_entries@entry_service.py:428-446` (`sum(all e.amount)` `:446`,
    the settle-time facet written to `actual_amount`).
  - C = SQL `func.sum(TransactionEntry.amount)@year_end_summary_service.py:519` + the credit
    `case(...)` `:520-528` (year-end `_compute_entry_breakdowns`).
  - Pairs A-B, A-C, B-C.
- Compared dimensions:
  - **Arithmetic: AGREE.** A `compute_entry_sums:363-367` partitions by `entry.is_credit`
    (`sum_credit += e.amount` if `is_credit` else `sum_debit += e.amount`); `total = debit +
    credit` (`:399`) == `sum(all e.amount)`. B `compute_actual_from_entries:446`
    `sum((e.amount for e in entries), Decimal("0"))` -- byte-identical arithmetic to A's
    `total`. C `func.sum(TransactionEntry.amount)` `:519` = the same total; C's
    `credit_total = func.sum(case(is_credit.is_(True) -> amount, else_=0))` `:520-528`
    reproduces A's `is_credit` partition exactly (same predicate). For the SAME transaction's
    entries A == B == C. A-01: all are raw sums of `Numeric(12,2)` operands (not re-quantized;
    A-01-acceptable raw-sum-into-display per inventory 1.7.6) -- not the axis.
  - **Scope: DIFFERENT by design (transaction universe, not per-txn arithmetic).** C is
    heavily filtered (`is_deleted False` `:545`, `transaction_type_id == EXPENSE` `:546`,
    `status_id.in_(settled)` `:547`, `is_envelope True` `:548`, year-windowed `period_ids`)
    -- a year-end settled-expense-envelope attribution aggregate. A/B operate on whatever
    `txn.entries` the caller loaded (grid/cell: typically Projected envelope txns; settle:
    the just-settled txn). The two measure the same per-txn quantity over DIFFERENT
    transaction SETS by design; for any single shared transaction they produce the identical
    total. Not a drift -- a scoped re-derivation (C) of the same arithmetic.
  - Q-14 cross-link: `compute_actual_from_entries:446` (settle write of `actual_amount`,
    F-027 row S12) and `build_entry_sums_dict.total` are byte-identical (`sum(all e.amount)`),
    so the `mark_paid`/`mark_done` settle asymmetry (Q-14, F-027 R4/R5 / A-14 proposed) does
    NOT introduce an `entry_sum_total` arithmetic divergence -- the value written is the same
    sum the display shows; the Q-14 axis is *whether* `settle_from_entries` runs, owned by
    F-027/Q-14, not an `entry_sum_total` drift.
- Verdict (`entry_sum_total`): **AGREE** on the per-transaction arithmetic (Python A == settle
  B == SQL C, all `sum(amount)` with the identical `is_credit` partition, Read at source).
  The SQL path's settled-expense-envelope-year scope is a by-design transaction-universe
  difference, NOT a per-txn drift -> SCOPE-by-design (recorded, not a finding-class drift).

#### entry_remaining

- Paths compared (P2-d/E1 `02_concepts.md:3252`):
  - P = sole producer `compute_remaining@entry_service.py:405-425`
    (`estimated_amount - sum(all e.amount)` `:424-425`).
  - J = Jinja mirror `remaining = t.estimated_amount - es.total`@`grid/_transaction_cell.html:21`
    and `grid/_mobile_grid.html:96,183` (`es.total` = `build_entry_sums_dict.total`).
  - Consumers A=`_entry_progress_fields@dashboard_service.py:239` (delegates to P),
    B=`_build_entry_data@companion.py:52` (delegates to P), C=`_render_entry_list@entries.py`
    (delegates to P).
  - Pairs P-J (numeric), and the Q-08 base.
- Compared dimensions:
  - **Numeric P vs J: AGREE.** `es.total` = `build_entry_sums_dict.total` = `debit + credit`
    = `sum(all e.amount)` = `compute_remaining`'s `total_spent` (`:424`). So `estimated_amount
    - es.total` (Jinja) is numerically IDENTICAL to `compute_remaining(estimated_amount,
  entries)`. The divergence is NOT a number -- it is the **E-16 coding-standards violation** (Jinja computes a money value), already recorded as F-027 rows **T2/T5** (same class as F-019`_escrow_list.html:37`);
  cross-ref, not a new numeric drift.
  - **Estimated-vs-actual base: UNKNOWN (Q-08).** `compute_remaining` always receives
    `txn.estimated_amount` (`dashboard_service.py:239`, `companion.py:52`, `entries.py`),
    never `actual_amount`, even for a settled (done) entry-tracked txn whose
    `actual_amount` was written by `settle_from_entries` -- this is exactly the Q-08
    ambiguity. The cross-anchor inconsistency (bill row `amount` = `effective_amount`/actual
    vs `entry_remaining` = estimated-based) is the **F-028 SILENT DIVERGE independent of
    Q-08**, already recorded in P3-c -- **cross-referenced, NOT re-derived here.**
  - The three consumers (A/B/C) all delegate to the single `compute_remaining` -> AGREE with
    each other (no per-consumer recompute; the only inline re-derivations are the Jinja
    mirrors J, numerically equal, E-16).
- Verdict (`entry_remaining`): **UNKNOWN** for the estimated-vs-actual base -- blocked on
  **Q-08** (`09_open_questions.md:288-323`, A-08 proposed). The numeric P-vs-J pair AGREES
  (Jinja mirror == `compute_remaining`, the divergence is the E-16 violation cross-ref
  F-027 T2/T5); the cross-anchor inconsistency is the recorded **F-028** SILENT DIVERGE
  (cross-ref, not re-derived).
- Overall verdict: **`entry_sum_total` AGREE** (Python==settle==SQL arithmetic; SCOPE
  by-design). **`entry_remaining` UNKNOWN** (Q-08; cross-ref F-028 for the
  already-recorded SILENT cross-anchor; cross-ref F-027 T2/T5 for the Jinja E-16). Gate-A
  correction CONFIRMED at source (`build_entry_sums_dict:392-402` is not an `entry_remaining`
  producer).
- If DIVERGE: `entry_remaining` estimated/actual axis conditional on Q-08 -> SILENT_DRIFT
  (F-028). The Jinja mirrors -> E-16 (F-027 T2/T5). `entry_sum_total` -> none (SCOPE
  by-design).
- Open questions for the developer: **Q-08** (governing `entry_remaining`'s base; A-08
  proposed). No new question (Q-08 already filed; the SQL-vs-Python `entry_sum_total` parity
  is a verification, not a "what is intended"; the Jinja E-16 is a coding-standards finding in
  F-027). Cross-link **F-028** (the escalated Q-08 entry-progress cluster -- authoritative for
  the cross-anchor), **F-027** rows S1/S7/S15/S17 (entries sub-transactional EQUIVALENT) and
  T2/T5 (the Jinja E-16 mirrors), **F-046** GP2 (shares `compute_remaining`/estimated base),
  **Q-14** (`mark_paid`/`mark_done` settle, A-14 proposed -- the `compute_actual_from_entries`
  write is byte-identical to the displayed total, so no `entry_sum_total` drift from Q-14).

---

### P3-d2 verification (a-h)

- **(a) Every E1 row in this family maps to a finding.** E1 rows this family = 17 (11
  multi-path `02_concepts.md:3245-3257` + 6 single-path `:3259-3271`); findings = 16
  (F-041..F-056, F-056 consolidating the 2 orphans). 15 1:1 + F-056 covering
  `entry_sum_total`+`entry_remaining` = 17 rows mapped. **E1 rows this family: 17; findings:
  16. Reconciled.** Zero skipped.
- **(b) growth / year_summary_growth / goal_progress each SPLIT per E2 before comparison;
  each consolidated finding has a sub-concept verdict table.** F-042 `growth` -> G1/G2/G3/G4
  (table; G3 explicitly flagged "MUST NOT be numerically compared with G1/G2/G4 (money)" --
  rate vs money, source-proven at `_safe_pct_change:482`). F-054 `year_summary_growth` ->
  YG1/YG2/YG3 (table; "NOT summable into one growth"). F-046 `goal_progress` -> GP1/GP2
  (table; "GP1 vs GP2 NOT numerically comparable"; GP1's ROUND_CEILING flagged
  documented-INTENTIONAL per Gate D, verified applied at the single
  `_compute_required_monthly:462-463` site, NOT flagged as an A-01 violation). The unsplit
  token was never compared. **HOLDS.**
- **(c) PA-04 body Read in FULL and the docstring-vs-code reconciliation stated explicitly.**
  `compute_slider_defaults@retirement_dashboard_service.py:257-332` Read in full (F-042).
  Reconciliation stated: docstring claims (1) all-Decimal and (2) zero-SWR-explicit are BOTH
  CONFIRMED in the body (`:307-309,:323-328` Decimal; `:304` `is None`) and the docstring
  does NOT overclaim; priors 0.6 "PA-04 open" is PARTIALLY STALE for those two claims (Phase-8
  note) BUT PA-04 is NOT fully remediated -- the sibling `compute_gap_data:220` (`or "0.04"`
  truthiness) and the `:321` zero-return-account truthiness exclusion are LIVE (worked
  examples in F-042). PA-05: slider-path named constants remediated, gap-path `"0.04"` magic
  literal live. **HOLDS.**
- **(d) PA-06: every apy_interest producer Read; cross-producer 365/366 verdict stated.**
  `interest_projection.calculate_interest` Read in full (`:1-114`); `grep` THIS session
  confirms the ONLY callers are `calculate_balances_with_interest@balance_calculator.py:161`
  (Read `:112-173`), `_compute_interest_for_year@year_end_summary_service.py:1245` (Read
  `:1207-1257`), `_compute_pre_anchor_interest@:1864` (Read `:1820-1873`) -- all delegate to
  the single engine. **Cross-producer verdict: CONSISTENT** -- one `DAYS_IN_YEAR=Decimal("365")`
  (`:44`), zero `366`, zero independent re-derivation; all producers make the SAME 365 daily
  assumption; the documented-accepted leap-year simplification is uniform, NOT a
  cross-producer SILENT_DRIFT (F-041). **HOLDS.**
- **(e) Every DIVERGE has a concrete worked example.** F-042 (`growth`): SWR slider 0.00% vs
  gap-math 4% -> $4,000/mo phantom income; zero-return exclusion 7.00% vs true 3.50%. F-043
  (`employer_contribution`): match employer, last limit-binding period -- card $240.00 vs
  chart/year-end $100.00. F-055 (`year_summary_employer_total`): same F-043 worked example
  (year-end total = capped $100.00 vs card uncapped $240.00). UNKNOWN-verdict findings
  (F-051/F-052 Q-15, F-054 YG1 Q-15, F-046 GP2 Q-08, F-056 `entry_remaining` Q-08) carry the
  source-proven facts and cross-ref the authoritative worked examples (F-006, F-028) rather
  than re-deriving them. **HOLDS.**
- **(f) Family-coverage tally maps EVERY E1 row (all families) to a P3 finding; orphans
  listed.** Delivered in the next subsection (49 E1 rows mapped a..d2; zero unmapped). **HOLDS.**
- **(g) Self spot-check -- 5 random findings re-Read at source THIS session (mix consolidated
  sub-rows + one-liners):**
  1. **F-041 AGREE** "`_compute_interest_for_year` sums per-period engine output": re-Read
     `year_end_summary_service.py:1245-1257` -> `_, interest_by_period =
     calculate_balances_with_interest(...)` `:1245`; `for period in all_periods: if
     period.start_date.year == year: total += interest_by_period.get(period.id, ZERO)`
     `:1254-1256`. **Confirmed** (annual == per-period sum by construction).
  2. **F-042 G3** "spending-trend is a percentage, not money": re-Read
     `spending_trend_service.py:481-482` -> `change = (last_predicted - first_predicted) /
     first_predicted * _HUNDRED; return change.quantize(_TWO_PLACES, ROUND_HALF_UP)`.
     **Confirmed** (a pct -> DEFINITION split from G1/G2/G4 money is source-justified).
  3. **F-043 DIVERGE** "card passes uncapped, chart passes limit-capped": re-Read
     `investment.py:183-189` -> `periodic_contribution = inputs.periodic_contribution`
     `:183`; `growth_engine.calculate_employer_contribution(employer_params,
     periodic_contribution)` `:187-188` (uncapped). Re-Read `growth_engine.py:258-265` ->
     `contribution = min(period_contrib_amount, remaining_limit)` `:259`;
     `employer_contribution = calculate_employer_contribution(employer_params, contribution)`
     `:265` (capped). **Confirmed.**
  4. **F-049 AGREE** "`calculate_benefit` annual quantize HALF_UP": re-Read
     `pension_calculator.py:61-67` -> `annual_benefit = (benefit_multiplier *
     years_of_service * high_avg).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)` `:61-63`;
     `monthly_benefit = (annual_benefit / 12).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)`
     `:65-67`. **Confirmed** (A-01-clean; monthly = annual/12).
  5. **F-056 `entry_remaining`** "`build_entry_sums_dict` has no remaining key (Gate-A
     correction)": re-Read `entry_service.py:392-402` -> returns `{"debit": debit, "credit":
     credit, "total": debit + credit, "count": len(txn.entries)}` -- no `remaining`, no
     `estimated_amount`. **Confirmed** (NOT an `entry_remaining` producer; sole producer is
     `compute_remaining:405-425`).
  Pass rate: **5/5.**
- **(h) Every UNKNOWN names the blocking Q-NN.** F-046 GP2 -> **Q-08**
  (`09_open_questions.md:288-323`, A-08 proposed; cross-ref F-028). F-051 -> **Q-15**
  (`:621-658`; cross-ref F-006). F-052 -> **Q-15** (cross-ref F-006/F-007). F-054 YG1 ->
  **Q-15** (cross-ref F-006/F-042 G4). F-055 -> **Q-15** (dispatcher; the cap divergence is
  F-043, not gated). F-056 `entry_remaining` -> **Q-08** (cross-ref F-028). Every UNKNOWN
  names a governing FILED Q (no new Q-NN raised by P3-d2 -- consistent with P2-d's zero new
  questions; the highest open question remains Q-17 from P3-b). **HOLDS.**

### Phase 3 family-coverage tally (P3-d2 owns this -- maps EVERY E1 row, all families)

Every E1 register row (`02_concepts.md:3215-3271`: 41 multi-path + 8 single-path = **49 E1 rows**;
the count is 47 cataloged concepts + the 2 Gate-A orphans `entry_sum_total` / `entry_remaining`
added by P2-reconcile Gate A) mapped to its P3 finding ID. Standalone audit-plan section-3.1
mandatory findings (no own E1 row) listed separately.
**Zero E1 row is an orphan -- every row is covered by some P3 session a..d2.**

#### Multi-path E1 rows (41) -> finding

| E1 row | Finding | Session |
| --- | --- | --- |
| `account_balance` | F-001 | P3-a |
| `checking_balance` | F-002 | P3-a |
| `projected_end_balance` | F-003 | P3-a |
| `period_subtotal` | F-004 | P3-a |
| `chart_balance_series` | F-005 | P3-a |
| `net_worth` | F-006 | P3-a |
| `savings_total` | F-007 | P3-a |
| `debt_total` | F-008 | P3-a |
| `monthly_payment` | F-013 | P3-b |
| `loan_principal_real` | F-014 | P3-b |
| `loan_principal_stored` | F-015 | P3-b |
| `loan_principal_displayed` | F-016 | P3-b |
| `principal_paid_per_period` | F-017 | P3-b |
| `interest_paid_per_period` | F-018 | P3-b |
| `escrow_per_period` | F-019 | P3-b |
| `total_interest` | F-020 | P3-b |
| `interest_saved` | F-021 | P3-b |
| `months_saved` | F-022 | P3-b |
| `payoff_date` | F-023 | P3-b |
| `dti_ratio` | F-025 | P3-b |
| `transfer_amount` | F-029 | P3-c |
| `effective_amount` | F-027 (+F-028 escalation) | P3-c |
| `paycheck_gross` | F-032 | P3-d1 |
| `paycheck_net` | F-033 | P3-d1 |
| `taxable_income` | F-034 | P3-d1 |
| `federal_tax` | F-035 | P3-d1 |
| `state_tax` | F-036 | P3-d1 |
| `fica` | F-037 | P3-d1 |
| `pre_tax_deduction` | F-038 | P3-d1 |
| `post_tax_deduction` | F-039 | P3-d1 |
| `apy_interest` | F-041 | P3-d2 |
| `growth` | F-042 | P3-d2 |
| `employer_contribution` | F-043 | P3-d2 |
| `goal_progress` | F-046 | P3-d2 |
| `entry_sum_total` | F-056 | P3-d2 |
| `entry_remaining` | F-056 | P3-d2 |
| `year_summary_jan1_balance` | F-051 | P3-d2 |
| `year_summary_dec31_balance` | F-052 | P3-d2 |
| `year_summary_principal_paid` | F-053 | P3-d2 |
| `year_summary_growth` | F-054 | P3-d2 |
| `year_summary_employer_total` | F-055 | P3-d2 |

#### Single-path internal-verify E1 rows (8) -> finding

| E1 row | Finding | Session |
| --- | --- | --- |
| `loan_remaining_months` | F-024 | P3-b |
| `transfer_amount_computed` | F-030 | P3-c |
| `contribution_limit_remaining` | F-044 | P3-d2 |
| `ytd_contributions` | F-045 | P3-d2 |
| `emergency_fund_coverage_months` | F-047 | P3-d2 |
| `cash_runway_days` | F-048 | P3-d2 |
| `pension_benefit_annual` | F-049 | P3-d2 |
| `pension_benefit_monthly` | F-050 | P3-d2 |

#### Standalone audit-plan section-3.1 mandatory findings (no own E1 row)

| Finding | What | Session |
| --- | --- | --- |
| F-009 | grid-projected vs `/savings`-checking (symptom #1) | P3-a |
| F-010 | `_sum_remaining` vs `_sum_all` (3.1 #3) | P3-a |
| F-011 | credit-status everywhere (3.1 #4) | P3-a |
| F-012 | shadow / Invariant 5 -- balance calculator (3.1 #5) | P3-a |
| F-026 | 5/5 ARM payment stability hand-computation (symptom #4) | P3-b |
| F-028 | entry-progress Q-08 cross-anchor cluster (escalated from F-027) | P3-c |
| F-031 | shadow / Invariant 5 -- ALL services + routes (extends F-012) | P3-c |
| F-040 | legacy `calculate_federal_tax` dead-code (governed) | P3-d1 |

**Tally reconciliation:** 41 multi-path + 8 single-path = **49 E1 rows, all mapped** (F-001.. F-056
less the standalone-only IDs; F-056 covers 2 orphan rows; `effective_amount` -> F-027 with the F-028
escalation). **8 standalone** section-3.1 findings carry no E1 row by design (the P3-a/b/c/d1
precedent: F-009/F-026/F-040 etc.). **Zero E1 row is unmapped; zero orphan.** Finding-ID span
F-001..F-056 is contiguous, no collisions across P3-a..P3-d2. P3-watchlist (plan-vs-code) and
P3-reconcile (the Phase-3 completion gate) remain and do not collide with this range.

### P3-d2 complete

P3-d2 complete (growth/retirement/savings/year-end family + the two Gate-A orphans, F-041..F-056;
the Phase-3 family-coverage tally delivered). Phase 3 is **NOT** complete -- **P3-watchlist** (the
plan-vs-code watchlist; NOT run here per the session mandate) and **P3-reconcile** (the Phase-3
completion gate; this session does NOT declare Phase 3 complete) remain. P3-a / P3-b / P3-c / P3-d1
/ P2 / P1 / priors content unmodified (append-only; the sole edit outside the new findings was the
`Finding IDs used` header). No source, test, or migration file modified this session. Not committed;
developer reviews between sessions.

---

## Phase 3 plan-vs-code watchlist triage (P3-watchlist, part 5a)

Session P3-watchlist-triage, 2026-05-16. This section is **triage only**: it does NOT assign fresh
HOLDS/VIOLATED by reading source. It bins every one of the 375 priors-0.4 watchlist entries
(W-001..W-375) into exactly one of COVERED / SUPERSEDED / NEEDS-COMPARISON / DUPLICATE-OF-0.3,
inheriting verdicts from the complete F-001..F-056 finding set above and from the resolved plan
adjudications A-01..A-07 (`09_open_questions.md`). It sizes the NEEDS-COMPARISON residual for the
comparison sub-sessions. No source/test/migration touched. This section is append-only and does NOT
modify F-001..F-056 or the `Finding IDs used` header; triage rows are W-NN-keyed, not F-NN findings.
P3-watchlist does NOT declare Phase 3 complete -- that is P3-reconcile's gate, after the comparison
sub-sessions run.

**Inheritance mapping used (audit plan section 3.1):** finding AGREE -> watchlist HOLDS; finding
DIVERGE -> VIOLATED (PARTIALLY_HOLDS where the finding explicitly shows the claim holds in part,
e.g. an ARM formula correct at its own sites while the cross-site concept diverges); finding
UNKNOWN/Q-NN -> UNKNOWN (same blocking Q-NN); finding DEAD_CODE -> VIOLATED-DEAD (intent not live).
SUPERSEDED applied BEFORE NEEDS-COMPARISON:
**all 24 `envelope_view` entries (W-127..W-150) are SUPERSEDED by A-02** (Option F /
`carry_fwd_impl` is current; `envelope_view`'s data model + aggregation helper were never built --
A-02 ACCURATE per auditor verification `09_open_questions.md:96-102`); W-133/W-134/W-147
additionally map C-02/A-03. These are NOT VIOLATED -- the code correctly does not implement a
superseded plan. DUPLICATE-OF-0.3 reserved for entries whose core claim literally restates a
resolved developer answer (A-01 rounding rule; E-01 transfer split; A-07/C-06 carry-forward
partition + pay_period_id branch; A-03/C-02 skip-on-override) -- cross-referenced, no separate
verdict.

### Triage table (375 rows, one per W-NN)

Legend: bin = COV(ERED) | SUP(ERSEDED) | NC (NEEDS-COMPARISON) | DUP-0.3. For COV: `ref` =
inheriting F-NN, `vd` = inherited verdict. For SUP: `ref` = superseding answer. For DUP-0.3: `ref` =
the 0.3/A-NN expectation. For NC: `note` = claim / code location the plan names / why no F-NN covers
it.

| W | plan | bin | ref | vd | note |
| -- | ---- | --- | --- | -- | ---- |
| W-001 | account_param_arch | NC | - | - | HysaParams apy Numeric(7,5) / compounding String; HysaParams model; schema-shape, no Phase-3 consistency finding (Phase 1/4) |
| W-002 | account_param_arch | NC | - | - | LoanParams tracks orig/current_principal/rate/term/orig_date/payment_day; LoanParams model; schema-shape, Phase 1/4 |
| W-003 | account_param_arch | NC | - | - | Retirement types share InvestmentParams cols; InvestmentParams model; schema-shape, Phase 1/4 |
| W-004 | account_param_arch | COV | F-041 | HOLDS | calculate_interest 5-arg signature; F-041 substrate cites `interest_projection.py:49-114` exactly that signature, single engine AGREE |
| W-005 | account_param_arch | NC | - | - | get_loan_projection exactly six attrs; amortization_engine.py; signature-exactness not verdicted by F-013 (reads params, not "exactly six") |
| W-006 | account_param_arch | COV | F-026 | VIOLATED | amortization must ignore ARM fields; F-026 proves arm_first/adjustment_months inert (consumed by 0 calc sites) and that inertness is the E-02 symptom-#4 root cause; superseded-by-arm_anchor intent, DIVERGE |
| W-007 | account_param_arch | COV | F-042 | HOLDS | growth_engine.project_balance signature; F-042 G1 reads `project_balance@:164-294` in full, engine signature consistent (the F-042 DIVERGE is the SWR slider, not the signature) |
| W-008 | account_param_arch | NC | - | - | retirement_gap_calculator must not read InvestmentParams directly; retirement_gap_calculator.py; SRP/DI claim, no Phase-3 finding (Phase 6) |
| W-009 | account_param_arch | COV | F-005 | HOLDS | HYSA detail calls calculate_balances_with_interest; F-005 path B `accounts.py:1425` confirms interest_detail routes through that engine |
| W-010 | account_param_arch | NC | - | - | hardcoded TRADITIONAL_TYPE_ENUMS frozenset at route; retirement.py; open-closed/SRP dispatch, no Phase-3 finding (Phase 6) |
| W-011 | account_param_arch | COV | F-005 | VIOLATED-DEAD | chart_data_service._calculate_account_balances route to interest; F-005 grep-proved `chart_data_service.py` removed in `e3b3a5e` -- intent not live |
| W-012 | account_param_arch | COV | F-005 | VIOLATED-DEAD | chart_data_service route to amortization; same DEAD chart_data_service (F-005) |
| W-013 | account_param_arch | NC | - | - | auto-create HysaParams on account create; accounts.py; creation wiring, no Phase-3 finding |
| W-014 | account_param_arch | NC | - | - | auto-create InvestmentParams hardcoded set; accounts.py; creation wiring, no finding |
| W-015 | account_param_arch | NC | - | - | account create redirect loan setup has_amortization; accounts.py; routing wiring, no finding |
| W-016 | account_param_arch | NC | - | - | savings dashboard batch-load HysaParams by type ID; savings.py; dispatch wiring (F-007 audits the aggregate not the batch-load filter) |
| W-017 | account_param_arch | NC | - | - | batch-load LoanParams has_amortization; savings.py; dispatch wiring, no finding |
| W-018 | account_param_arch | NC | - | - | batch-load InvestmentParams hardcoded set; savings.py; dispatch wiring, no finding |
| W-019 | account_param_arch | NC | - | - | Option A InterestParams nullable maturity_date/term_months; interest_params table; conditional planned, schema, no finding |
| W-020 | account_param_arch | NC | - | - | Option D category-based investment dispatch; accounts.py/savings.py; conditional planned, no finding |
| W-021 | account_param_arch | NC | - | - | replace HYSA type-ID checks with has_interest flag; accounts/chart_data/savings; planned dispatch refactor, no finding |
| W-022 | account_param_arch | NC | - | - | replace investment type-ID sets with category queries; accounts/savings; planned dispatch refactor, no finding |
| W-023 | account_params | NC | - | - | interest-bearing duck-typed routing; amortization_engine/balance_calculator; dispatch, no finding |
| W-024 | account_params | NC | - | - | has_parameters auto-create rows; accounts.py:137,159,576; creation wiring, no finding |
| W-025 | account_params | NC | - | - | HYSA/MM/CD/HSA unified interest projection when has_interest; interest_projection.py; dispatch (F-041 audits engine arithmetic not the has_interest routing) |
| W-026 | account_params | NC | - | - | Traditional 401k/IRA pre-tax via metadata flag; retirement.py:51,131-132,332; dispatch flag, no finding |
| W-027 | account_params | NC | - | - | is_liquid contributes emergency fund + dropdowns; savings.py:433-436; liquid-set membership wiring (F-047 audits the months formula not the set) |
| W-028 | account_params | NC | - | - | InterestParams stores APY/compounding; loan_params.py; schema, no finding (Phase 1/4) |
| W-029 | account_params | NC | - | - | is_pretax differs in retirement gap; retirement.py; dispatch flag, no finding |
| W-030 | account_params | NC | - | - | has_interest default false seed (HYSA/HSA true); ref_seeds.py; seed data, no finding |
| W-031 | account_params | NC | - | - | is_pretax default false seed; ref_seeds.py; seed data, no finding |
| W-032 | account_params | NC | - | - | HSA has_parameters True; ref_seeds.py/accounts.py; seed/dispatch, no finding |
| W-033 | account_params | NC | - | - | is_liquid identifies Checking/Savings/HYSA/MM; ref_seeds.py; seed data, no finding |
| W-034 | account_params | NC | - | - | auto-create InterestParams/InvestmentParams not loan; accounts.py:132-173; creation wiring, no finding |
| W-035 | account_params | COV | F-005 | VIOLATED-DEAD | chart data dispatch calculate_balances_with_interest; chart_data_service DEAD (F-005) |
| W-036 | account_params | COV | F-005 | VIOLATED-DEAD | chart data dispatch calculate_balances_with_amortization; chart_data_service DEAD (F-005) |
| W-037 | account_params | NC | - | - | investment auto-create only has_parameters not interest not amortizing; accounts.py:141-151; creation wiring, no finding |
| W-038 | account_params | NC | - | - | savings dashboard needs_setup unified metadata flags; savings.py:299-305; dispatch wiring, no finding |
| W-039 | account_params | NC | - | - | retirement gap dispatch is_pretax flag; retirement.py:131-332; dispatch flag, no finding |
| W-040 | account_params | NC | - | - | 529 Plan auto-create InvestmentParams; accounts.py/chart_data_service.py; creation wiring, no finding |
| W-041 | account_params | NC | - | - | no calc services modified only dispatch; multi-engine; meta-claim about refactor, no finding |
| W-042 | arm_anchor | COV | F-013 | HOLDS | balance reset to anchor_balance at anchor; F-013 site 3 anchor-reset exists, A-05 confirms anchor-reset method is current |
| W-043 | arm_anchor | COV | F-013 | HOLDS | pre-anchor approximate / post-anchor exact; F-013 substrate + A-05 (anchor-reset current) |
| W-044 | arm_anchor | COV | F-026 | PARTIALLY_HOLDS | monthly payment recalc from anchor forward; recalc exists (A-05) but F-026 shows the anchor (frozen stored current_principal) over shrinking remaining = E-02 symptom #4 |
| W-045 | arm_anchor | COV | F-013 | HOLDS | total interest/payoff derivable without regenerating; F-013/F-020 substrate: _derive_summary_metrics derives from the single schedule |
| W-046 | arm_anchor | COV | F-014 | HOLDS | LoanProjection.current_balance = anchor(ARM)/schedule(fixed); F-014 `amortization_engine.py:977-984`, A-04 makes the dual policy INTENDED |
| W-047 | arm_anchor | COV | F-014 | HOLDS | ARM get_loan_projection passes anchor=current_principal date=today; F-014/F-013 `amortization_engine.py:926`, A-04/A-05 confirm |
| W-048 | arm_anchor | COV | F-013 | PARTIALLY_HOLDS | ARM monthly = calc(current_principal,current_rate,remaining); F-013 names W-048; A-05 confirms ARM formula at the 8 ARM sites, other sites diverge |
| W-049 | arm_anchor | COV | F-013 | PARTIALLY_HOLDS | fixed monthly = calc(original_principal,rate,term); F-013 fixed ELSE sites 8/10/12/15 use it; 16-site cross divergence |
| W-050 | arm_anchor | NC | - | - | schedule generated exactly once in get_loan_projection; amortization_engine.py; generation-count efficiency claim, no finding verdicts "once not twice" |
| W-051 | arm_anchor | NC | - | - | calculate_summary accepts+threads anchor to both generate_schedule; amortization_engine.py; anchor-threading not verdicted (F-023 only shows calculate_payoff_by_date lacks anchor) |
| W-052 | arm_anchor | COV | F-018 | HOLDS | load_loan_context loads projection data once+shared; F-017/F-018 rely on load_loan_context as the shared A-06 source (AGREE-by-construction) |
| W-053 | arm_anchor | COV | F-014 | HOLDS | floor schedule ARM anchor=current_principal today; F-014 ARM anchor mechanism (A-04) |
| W-054 | arm_anchor | COV | F-021 | HOLDS | original schedule no anchor params; F-021 path B / F-023 path B confirm contractual baseline omits payments/anchor by design |
| W-055 | arm_anchor | NC | - | - | extra_payment mode calculate_summary passes anchor ARM; loan.payoff_calculate; anchor-threading in payoff_calculate not verdicted by F-021 |
| W-056 | arm_anchor | NC | - | - | committed/accelerated ARM pass anchor; loan.payoff_calculate; same anchor-threading detail not verdicted |
| W-057 | arm_anchor | COV | F-016 | UNKNOWN(Q-11) | target_date real_principal uses proj.current_balance; F-016 P4 `loan.py:1087`; principal-display PRIMARY-PATH UNKNOWN |
| W-058 | arm_anchor | COV | F-014 | UNKNOWN(Q-15) | savings dashboard current balance from proj.current_balance; F-003/F-014 `savings_dashboard_service.py:373`; canonical-base UNKNOWN |
| W-059 | arm_anchor | COV | F-053 | HOLDS | year-end ARM debt schedules pass anchor; F-017/F-053 `_generate_debt_schedules` ARM anchor `:1465-1483` confirmed by construction |
| W-060 | arm_anchor | COV | F-014 | HOLDS | ARM _compute_real_principal returns current_principal directly; C-03 resolved by A-04 (ARM uses stored), F-014 path C `debt_strategy.py:172-173` |
| W-061 | arm_anchor | NC | - | - | ARM schedule balance decreases monotonically; analytics/dashboard; balance-trajectory verification, F-026 covers the payment not the monotonicity |
| W-062 | arm_anchor | COV | F-052 | UNKNOWN(Q-15) | year-end Dec31 < current for ARM; F-052 year_summary_dec31_balance Q-15-gated |
| W-063 | arm_anchor | COV | F-053 | HOLDS | principal paid Dec31->today positive; F-053 year_summary_principal_paid AGREE-by-construction |
| W-064 | calendar_totals | NC | - | - | inline totals show only non-zero lines; _calendar_month.html; calendar UI display, no Phase-3 finding (calendar_service/analytics out of balance family) |
| W-065 | calendar_totals | NC | - | - | per-day income_total/expense_total in _build_calendar_weeks; analytics.py; per-day money sum, no F-NN audits calendar day totals |
| W-066 | calendar_totals | NC | - | - | remove popover_html key; analytics.py; UI refactor, no finding |
| W-067 | calendar_totals | NC | - | - | delete _build_popover_html; analytics.py; UI refactor, no finding |
| W-068 | calendar_totals | NC | - | - | render inline income green non-zero; _calendar_month.html; UI, no finding |
| W-069 | calendar_totals | NC | - | - | render inline expense red non-zero; _calendar_month.html; UI, no finding |
| W-070 | calendar_totals | NC | - | - | whole-dollar {:,.0f}; _calendar_month.html; UI formatting, no finding |
| W-071 | calendar_totals | NC | - | - | data-day + role=button; _calendar_month.html; UI, no finding |
| W-072 | calendar_totals | NC | - | - | <template data-detail-day> pre-rendered; _calendar_month.html; UI, no finding |
| W-073 | calendar_totals | NC | - | - | detail table {:,.2f}; _calendar_month.html; UI formatting, no finding |
| W-074 | calendar_totals | NC | - | - | status badge Paid/Projected; _calendar_month.html; UI, no finding |
| W-075 | calendar_totals | NC | - | - | JS bind click handlers htmx:afterSettle; calendar.js; JS UI, no finding |
| W-076 | calendar_totals | NC | - | - | clone template detail into #calendar-day-detail; calendar.js; JS UI, no finding |
| W-077 | calendar_totals | NC | - | - | double-click toggle off; calendar.js; JS UI, no finding |
| W-078 | calendar_totals | NC | - | - | different-day deselect prev; calendar.js; JS UI, no finding |
| W-079 | calendar_totals | NC | - | - | close button dismiss; calendar.js; JS UI, no finding |
| W-080 | calendar_totals | NC | - | - | remove popover/bootstrap.Popover; calendar.js; JS UI refactor, no finding |
| W-081 | calendar_totals | NC | - | - | no data-bs-toggle=popover in HTML; _calendar_month.html; UI, no finding |
| W-082 | calendar_totals | NC | - | - | existing tests pass; test_analytics.py; test, no finding |
| W-083 | calendar_totals | NC | - | - | new tests verify inline totals classes; test_analytics.py; test, no finding |
| W-084 | calendar_totals | NC | - | - | new tests verify <template> txn names; test_analytics.py; test, no finding |
| W-085 | carry_fwd_design | NC | - | - | multi-hop re-apply settle-and-roll incl prior bumps; _settle_source_and_roll; carry-forward mechanic, no dedicated F-NN (Option F current per A-02; needs code read) |
| W-086 | carry_fwd_design | NC | - | - | wife overspent: settled actual=entry sum, no bump; _settle_source_and_roll; carry-forward mechanic, no finding |
| W-087 | carry_fwd_design | NC | - | - | wife zero: settled actual=0, full estimate carries; _settle_source_and_roll; carry-forward mechanic, no finding |
| W-088 | carry_fwd_design | NC | - | - | untracked templates 33cd21e move-whole+is_override; carry_forward_service; mechanic, no finding |
| W-089 | carry_fwd_design | NC | - | - | ad-hoc rows 33cd21e unchanged; carry_forward_service; mechanic, no finding |
| W-090 | carry_fwd_design | NC | - | - | shadow transfers 33cd21e unchanged; carry_forward_service; mechanic, no finding (A-07 partition is principle, branch impl not verdicted) |
| W-091 | carry_fwd_design | COV | F-002 | HOLDS | settled source rows excluded from period subtotal; F-002 explicitly "A-02/W-091/W-092 settled-source exclusion holds (verified at the gate)" |
| W-092 | carry_fwd_design | COV | F-002 | HOLDS | settled Done/Received/Settled excluded from balance projection; F-002 verified at the Projected-only gate |
| W-093 | carry_fwd_design | COV | F-004 | HOLDS | period subtotal sums only Projected via effective_amount; F-004 D1 `grid.py:263-279` Projected-only confirmed |
| W-094 | carry_fwd_design | NC | - | - | entries_sum from source.entries; _settle_source_and_roll; carry-forward mechanic, no finding |
| W-095 | carry_fwd_design | NC | - | - | source settled DONE/RECEIVED actual=entries_sum; _settle_source_and_roll; C-01 winning side (A-02), mechanic not verdicted by a finding |
| W-096 | carry_fwd_design | NC | - | - | source paid_at=now; _settle_source_and_roll; C-01 winning side, mechanic, no finding |
| W-097 | carry_fwd_design | NC | - | - | leftover = max(0, estimated - entries_sum); _settle_source_and_roll; mechanic, no finding |
| W-098 | carry_fwd_design | NC | - | - | target canonical estimated += leftover; _settle_source_and_roll; mechanic, no finding |
| W-099 | carry_fwd_design | NC | - | - | target canonical is_override=True; _settle_source_and_roll; mechanic, no finding |
| W-100 | carry_fwd_design | NC | - | - | missing canonical: run recurrence then bump; _settle_source_and_roll; mechanic, no finding |
| W-101 | carry_fwd_design | NC | - | - | envelope rows no sibling rows; carry_forward_service; C-01 winning side (A-02 no-sibling), branch impl not verdicted by a finding |
| W-102 | carry_fwd_design | NC | - | - | period subtotal +exactly leftover; unspecified; verification claim, no finding |
| W-103 | carry_fwd_design | NC | - | - | balance projection -leftover in target; unspecified; verification claim, no finding |
| W-104 | carry_fwd_design | NC | - | - | net forward cash flow unchanged; unspecified; verification claim, no finding |
| W-105 | carry_fwd_impl | COV | F-004 | UNKNOWN(Q-10) | identical totals across cell/subtotal/balance; F-002 Pair C / F-004 D1-D2 same-page subtotal-vs-balance is exactly the Q-10-governed question |
| W-106 | carry_fwd_impl | NC | - | - | missing canonical via generate_for_template then bump; recurrence_engine.generate_for_template; carry-forward mechanic, no finding |
| W-107 | carry_fwd_impl | NC | - | - | already-settled target refuse + atomic batch fail; carry_forward_service.py; mechanic, no finding |
| W-108 | carry_fwd_impl | NC | - | - | is_envelope=True rejected on income templates; schemas/validation.py; validation, no finding |
| W-109 | carry_fwd_impl | NC | - | - | transfer_id NOT NULL stays 33cd21e path; carry_forward_service.py; mechanic, no finding (A-07 principle) |
| W-110 | carry_fwd_impl | NC | - | - | partition transfer-status then is_envelope; carry_forward_service.py; partition mechanic, A-07 principle but branch impl not verdicted |
| W-111 | carry_fwd_impl | NC | - | - | shared settle_from_entries extracted; transaction_service.py; refactor structure, F-027 S12 EQUIVALENT only as a write |
| W-112 | carry_fwd_impl | NC | - | - | settle_from_entries sets actual=sum,status,paid_at; transaction_service.py; mechanic (F-027 S12 EQUIVALENT but not the per-claim verify) |
| W-113 | carry_fwd_impl | NC | - | - | settle_from_entries not transfers + requires is_envelope; transaction_service.py; guard mechanic, no finding |
| W-114 | carry_fwd_impl | NC | - | - | entries_sum via compute_actual_from_entries; carry_forward_service.py; mechanic, no finding |
| W-115 | carry_fwd_impl | NC | - | - | settled target ValidationError + batch fail; carry_forward_service.py; mechanic, no finding |
| W-116 | carry_fwd_impl | NC | - | - | leftover = max(0, source.estimated - entries_sum); carry_forward_service.py; mechanic, no finding |
| W-117 | carry_fwd_impl | NC | - | - | leftover>0: canonical estimated += leftover, is_override; carry_forward_service.py; mechanic, no finding |
| W-118 | carry_fwd_impl | DUP-0.3 | A-07/C-06 | xref | envelope source pay_period_id unchanged; C-06 resolved by A-07 (envelope branch stays in source period) -- the resolved answer, cross-ref A-07 |
| W-119 | carry_fwd_impl | DUP-0.3 | A-07/C-06 | xref | discrete rows moved whole pay_period=target+is_override; A-07 verification Read `carry_forward_service.py:415-416` and confirmed discrete branch -- the resolved answer |
| W-120 | carry_fwd_impl | DUP-0.3 | A-03/C-02 | xref | is_override blocks regeneration (skip-on-override); C-02 resolved by A-03 (continue blocking on any is_override; envelope_view narrowing never built) |
| W-121 | carry_fwd_impl | NC | - | - | preview computes same partition + per-row actions; carry-forward-preview route; preview mechanic, no finding |
| W-122 | carry_fwd_impl | NC | - | - | BLOCKED -> Confirm disabled + atomic refuse; transactions.py/_carry_forward_preview_modal.html; UI+mechanic, no finding |
| W-123 | carry_fwd_impl | NC | - | - | empty generate_for_template -> ValidationError + atomic; carry_forward_service.py; mechanic, no finding |
| W-124 | carry_fwd_impl | NC | - | - | all ValidationErrors rollback batch; transactions.py; route error handling, no finding |
| W-125 | carry_fwd_impl | NC | - | - | post-CF state: source DONE $65, target $135 is_override, subtotal +$35, balance -$135; carry_forward_service/transaction/balance_calculator; multi-mechanic verification, no single finding |
| W-126 | carry_fwd_impl | NC | - | - | income+is_envelope POST -> 400; schemas/validation.py; validation, no finding |
| W-127 | envelope_view | SUP | A-02 | n/a | wife sees 18/135 combined envelope; A-02: envelope_view superseded, goal reached by Option F bumped canonical -- code correctly does not implement a superseded plan |
| W-128 | envelope_view | SUP | A-02/C-01 | n/a | carried-row entries not counted in envelope_spent; A-02 superseded (no carried-member data model) |
| W-129 | envelope_view | SUP | A-02 | n/a | manually-edited canonical entries count; A-02 superseded |
| W-130 | envelope_view | SUP | A-02/C-01 | n/a | carried_from_period_id FK on transactions/transfers; A-02: zero `carried_from_period_id` in code/migrations -- never built |
| W-131 | envelope_view | SUP | A-02/C-01 | n/a | CF sets carried_from_period_id=source; A-02 superseded |
| W-132 | envelope_view | SUP | A-02 | n/a | shadow transfers propagate carried_from_period_id; A-02 superseded |
| W-133 | envelope_view | SUP | A-03/C-02 | n/a | recurrence skip only non-carried override; A-03: narrowing never implemented (continue blocking on any is_override) |
| W-134 | envelope_view | SUP | A-03/C-02 | n/a | transfer recurrence same skip change; A-03 superseded |
| W-135 | envelope_view | SUP | A-02 | n/a | envelope_budget formula; A-02: EnvelopeCell never built |
| W-136 | envelope_view | SUP | A-02 | n/a | envelope_spent = entry_sums[canonical]; A-02 superseded |
| W-137 | envelope_view | SUP | A-02 | n/a | settled canonical envelope_spent fallback; A-02 superseded |
| W-138 | envelope_view | SUP | A-02 | n/a | single-member EnvelopeCell; A-02: no EnvelopeCell anywhere |
| W-139 | envelope_view | SUP | A-02 | n/a | summary_status_id badge; A-02 superseded |
| W-140 | envelope_view | SUP | A-02 | n/a | override indicator on is_override OR carried; A-02 superseded |
| W-141 | envelope_view | SUP | A-02 | n/a | click handler canonical/first-carried; A-02 superseded |
| W-142 | envelope_view | SUP | A-02 | n/a | cell DOM id #envelope-cell-{period}-{template}; A-02 superseded |
| W-143 | envelope_view | SUP | A-02 | n/a | quick-edit rebuild EnvelopeCell + swap; A-02 superseded |
| W-144 | envelope_view | SUP | A-02 | n/a | entry form txn_id=primary_member_id; A-02 superseded |
| W-145 | envelope_view | SUP | A-02/C-01 | n/a | subtotals sum all non-deleted while cell shows experiential; A-02 superseded (Option F bumps canonical) |
| W-146 | envelope_view | SUP | A-02 | n/a | carried_from_period_id == source on moved row; A-02 superseded |
| W-147 | envelope_view | SUP | A-03/C-02 | n/a | carried row alone not skip generation; A-03 superseded |
| W-148 | envelope_view | SUP | A-02 | n/a | envelope cell combined progress single/multi-hop/carried; A-02 superseded |
| W-149 | envelope_view | SUP | A-02 | n/a | quick-edit rebuild envelope not canonical; A-02 superseded |
| W-150 | envelope_view | SUP | A-02 | n/a | companion envelope card combined progress; A-02 superseded |
| W-151 | net_worth_amort | COV | F-006 | UNKNOWN(Q-15) | net-worth investment include return+employer; F-006 investment-branch input divergence, Q-15-gated |
| W-152 | net_worth_amort | COV | F-006 | UNKNOWN(Q-15) | net worth + savings progress identical paths; F-006 names W-152 PLAN_DRIFT, dual dispatch, Q-15 |
| W-153 | net_worth_amort | COV | F-006 | UNKNOWN(Q-15) | remove _get_account_balance_map amortization fallback; F-006 `_get_account_balance_map@:2036` input, Q-15 |
| W-154 | net_worth_amort | COV | F-006 | UNKNOWN(Q-15) | degrade to anchor+txns without debt_schedules; F-006 input dispatch, Q-15 |
| W-155 | net_worth_amort | COV | F-006 | UNKNOWN(Q-15) | net worth investment increases over time; F-006 investment branch, Q-15 |
| W-156 | net_worth_amort | COV | F-006 | UNKNOWN(Q-15) | 401k $10k 7% Month5 > $11k; F-006 investment branch test-behavior, Q-15 |
| W-157 | net_worth_amort | COV | F-006 | UNKNOWN(Q-15) | employer contributions faster growth; F-006 investment branch, Q-15 |
| W-158 | net_worth_amort | COV | F-006 | UNKNOWN(Q-15) | mixed types correct calc paths; F-006 dual dispatch, Q-15 |
| W-159 | net_worth_amort | COV | F-052 | UNKNOWN(Q-15) | Dec31 net worth == savings progress investment; F-006/F-052 W-159 equality not enforced, Q-15 |
| W-160 | net_worth_amort | COV | F-006 | UNKNOWN(Q-15) | mortgage no debt_schedules -> plain balance; F-006 loan-branch input, Q-15 |
| W-161 | net_worth_amort | COV | F-006 | UNKNOWN(Q-15) | _compute_net_worth/_build_account_data/_get_account_balance_map ctx param; F-006 dispatch, Q-15 |
| W-162 | net_worth_amort | COV | F-006 | UNKNOWN(Q-15) | ctx threaded through call chain; F-006 dispatch, Q-15 |
| W-163 | net_worth_amort | COV | F-054 | UNKNOWN(Q-15) | investment dispatch to growth engine when ctx; F-054 YG1 `_get_account_balance_map@:1064`, Q-15 |
| W-164 | net_worth_amort | COV | F-054 | UNKNOWN(Q-15) | _build_investment_balance_map calculate_balances pre-anchor; F-054 YG1 base, Q-15 |
| W-165 | net_worth_amort | COV | F-054 | UNKNOWN(Q-15) | growth engine post-anchor incl employer+return; F-054 YG1, Q-15 |
| W-166 | net_worth_amort | COV | F-054 | UNKNOWN(Q-15) | _build_investment_balance_map OrderedDict; F-054 YG1 producer, Q-15 |
| W-167 | net_worth_amort | COV | F-006 | UNKNOWN(Q-15) | debt no debt_schedules degrade anchor+txns; F-006 loan-branch input, Q-15 |
| W-168 | net_worth_amort | COV | F-006 | UNKNOWN(Q-15) | _build_summary passes ctx to _compute_net_worth; F-006 dispatch, Q-15 |
| W-169 | net_worth_amort | NC | - | - | calculate_balances_with_amortization remains + 17 tests; balance_calculator.py; test-coverage retention claim, Phase-7 not Phase-3 consistency |
| W-170 | phase8_hardening | NC | - | - | audit_log row-level INSERT/UPDATE/DELETE old/new JSONB; system.audit_log; audit infra, out of financial-calc scope, no finding |
| W-171 | phase8_hardening | NC | - | - | audit_log changed_fields only; system.audit_log; audit infra, no finding |
| W-172 | phase8_hardening | NC | - | - | audit trigger captures app.current_user_id; trigger fn; audit infra, no finding |
| W-173 | phase8_hardening | NC | - | - | txn CRUD structured JSON log; unspecified; observability, no finding |
| W-174 | phase8_hardening | NC | - | - | transfer create structured log; unspecified; observability, no finding |
| W-175 | phase8_hardening | NC | - | - | anchor update structured log; unspecified; observability, no finding |
| W-176 | phase8_hardening | NC | - | - | carry forward structured log; unspecified; observability, no finding |
| W-177 | phase8_hardening | NC | - | - | recurrence regen structured log; unspecified; observability, no finding |
| W-178 | phase8_hardening | NC | - | - | audit retention deletes >365d; scripts/audit_cleanup.py; ops script, no finding |
| W-179 | phase8_hardening | NC | - | - | every budget/salary query filters user_id; all services; IDOR/security, out of financial-calc scope, no finding |
| W-180 | phase8b | NC | - | - | UPDATE skip audit if no change; audit_trigger_func; audit infra, no finding |
| W-181 | phase8b | NC | - | - | DELETE records old_data; audit_trigger_func; audit infra, no finding |
| W-182 | phase8b | NC | - | - | INSERT records new_data; audit_trigger_func; audit infra, no finding |
| W-183 | prod_impl | NC | - | - | carry-forward filter by scenario_id; carry_forward_service.py:62-69; F-029 explicitly defers carry_forward_unpaid scenario filter to Phase 7/8, not Phase-3 |
| W-184 | prod_impl | COV | F-001 | HOLDS | post-anchor exclude done/received; F-001/F-010/F-011 balance engine Projected-only gate (`status_id != projected`) excludes done/received, AGREE |
| W-185 | prod_impl | NC | - | - | stale_anchor_warning flag when done/received post-anchor; balance_calculator.py; stale-detection, Phase 4 (`04_source_of_truth.md`) not Phase-3 |
| W-186 | prod_readiness_v1 | NC | - | - | conftest includes settled status; tests/conftest.py; test fixture, no finding |
| W-187 | prod_readiness_v1 | DUP-0.3 | A-01 | xref | amortization payment Decimal quantize TWO_PLACES ROUND_HALF_UP; literal A-01 restatement; xref F-013 substrate (engine def quantizes HALF_UP `:192/197`, A-01-clean) |
| W-188 | prod_readiness_v1 | COV | F-005 | VIOLATED-DEAD | float conversions only at presentation in chart_data_service; chart_data_service DEAD (F-005) |
| W-189 | prod_readiness_v1 | NC | - | - | float in route chart files isolated+commented; auto_loan/mortgage/retirement.py; coding-standards E-10, no Phase-3 finding verdicts these routes |
| W-190 | prod_readiness_v1 | NC | - | - | carry_forward_unpaid verify both period ownership; carry_forward_service.py; carry-forward, F-029 defers carry_forward_unpaid to Phase 7/8 |
| W-191 | prod_readiness_v1 | NC | - | - | carry_forward_unpaid skip done/cancelled/credit; carry_forward_service.py; carry-forward mechanic, deferred |
| W-192 | prod_readiness_v1 | DUP-0.3 | A-07/C-06 | xref | carry_forward_unpaid flag template_id as override when moved; C-06 resolved by A-07 (discrete-branch behavior) -- the resolved answer |
| W-193 | prod_readiness_v1 | NC | - | - | carry_forward_unpaid skip deleted; carry_forward_service.py; mechanic, deferred |
| W-194 | prod_readiness_v1 | NC | - | - | carry_forward_unpaid return moved count; carry_forward_service.py; mechanic, deferred |
| W-195 | prod_readiness_v1 | NC | - | - | DevConfig DATABASE_URL fallback; config.py; config, out of scope, no finding |
| W-196 | prod_readiness_v1 | NC | - | - | seed_user reject <12 char; scripts/seed_user.py; script, out of scope, no finding |
| W-197 | prod_readiness_v1 | NC | - | - | seed_user 12-char min == registration; scripts/seed_user.py; script, out of scope, no finding |
| W-198 | req_v3_addendum | COV | F-041 | HOLDS | interest projected per period HYSA add after txns; F-041 single interest engine AGREE |
| W-199 | req_v3_addendum | COV | F-041 | HOLDS | daily compounding daily_rate=apy/365 formula; F-041 daily branch `interest_projection.py:89` |
| W-200 | req_v3_addendum | COV | F-041 | HOLDS | monthly compounding formula; F-041 monthly branch `:93-96` |
| W-201 | req_v3_addendum | COV | F-019 | HOLDS | monthly escrow = sum(annual/12); F-019 escrow_calculator `:54-55` AGREE |
| W-202 | req_v3_addendum | COV | F-019 | HOLDS | escrow component independent inflation; F-019 escrow_calculator inflation path `:35-52` |
| W-203 | req_v3_addendum | COV | F-013 | HOLDS | amortization monthly_payment annuity formula; F-013 "the single annuity formula at `:178-197` is correct" |
| W-204 | req_v3_addendum | COV | F-017 | PARTIALLY_HOLDS | per-month interest/principal split; F-017 engine path A formula correct, cross-path (B vs A/C) divergence |
| W-205 | req_v3_addendum | DUP-0.3 | E-01 | xref | mortgage payment transfer: full to checking, principal-only to mortgage; literal E-01 restatement |
| W-206 | req_v3_addendum | DUP-0.3 | E-01 | xref | transfer stores full, balance calc applies principal portion; literal E-01 restatement; xref F-017 |
| W-207 | req_v3_addendum | NC | - | - | deduction target_account_id auto-applied as income; paycheck receipt; receipt-processing mechanic, no finding |
| W-208 | req_v3_addendum | COV | F-042 | HOLDS | growth before contribution; period_return_rate formula; F-042 G1 project_balance single engine AGREE |
| W-209 | req_v3_addendum | COV | F-043 | HOLDS | contribution = MIN(periodic, remaining_limit) year reset; F-043 `growth_engine.py:259-262` cap logic |
| W-210 | req_v3_addendum | COV | F-043 | PARTIALLY_HOLDS | employer separate credited; F-043 confirms employer added in loop; card-vs-chart cap DIVERGE |
| W-211 | req_v3_addendum | COV | F-043 | PARTIALLY_HOLDS | cap projections at annual limit; projection IS capped (growth_engine); F-043 card not capping |
| W-212 | req_v3_addendum | COV | F-043 | HOLDS | flat pct employer = gross*flat_pct; F-043 calculate_employer_contribution flat branch |
| W-213 | req_v3_addendum | COV | F-043 | HOLDS | match employer formula; F-043 substrate confirms `:116-125` match formula |
| W-214 | req_v3_addendum | COV | F-049 | HOLDS | pension annual = mult*yos*high_avg; F-049 calculate_benefit AGREE A-01-clean |
| W-215 | req_v3_addendum | NC | - | - | monthly income gap = pre-ret net monthly - pension; retirement_gap_calculator.py; gap subtraction is consumer-resident, F-049/F-050 only verify pension pass-through |
| W-216 | req_v3_addendum | COV | F-042 | VIOLATED | required savings = gap*12/SWR; F-042 SWR cross-anchor SILENT_DRIFT (`compute_gap_data:220` `or "0.04"` vs slider `is None`) |
| W-217 | req_v3_addendum | COV | F-007 | UNKNOWN(Q-15) | projected total savings = sum projected balances; F-007 savings_total multi-aggregator Q-15 |
| W-218 | section5 | COV | F-017 | HOLDS | engine apply actual payment amounts; F-017 path A applies PaymentRecord; A-05 replay current |
| W-219 | section5 | NC | - | - | remaining<payment: interest on smaller, excess to zero; generate_schedule; engine edge mechanic not verdicted by a consistency finding |
| W-220 | section5 | COV | F-014 | HOLDS | is_confirmed flag distinguishes confirmed/projected; F-014 path A uses `row.is_confirmed` |
| W-221 | section5 | COV | F-014 | HOLDS | dashboard query only shadow income as confirmed; F-014/get_payment_history shadow-income filter |
| W-222 | section5 | COV | F-014 | PARTIALLY_HOLDS | current principal from confirmed payments replace static; C-03 resolved A-04 (fixed replays, ARM stored); F-014 card renders STORED (symptom #3) |
| W-223 | section5 | COV | F-030 | HOLDS | monthly payment incl P&I + escrow; F-030 transfer_amount_computed prefill = monthly_payment+escrow AGREE |
| W-224 | section5 | COV | F-045 | HOLDS | growth engine apply contribution from list; F-045/F-042 contribution timeline |
| W-225 | section5 | COV | F-045 | HOLDS | same-date contributions summed; F-045 ytd/contribution timeline |
| W-226 | section5 | COV | F-045 | HOLDS | paycheck deduction contributions per-period confirmed past; F-045 `investment_projection.py:175-187` |
| W-227 | section5 | COV | F-043 | PARTIALLY_HOLDS | employer on period-specific amount; F-043 employer on capped contribution `:265`, card-vs-chart DIVERGE |
| W-228 | section5 | COV | F-043 | HOLDS | what-if respect annual limit cap; F-043/F-042 limit cap logic |
| W-229 | section5 | COV | F-043 | PARTIALLY_HOLDS | employer match recalc for hypothetical; F-043 match branch, cap divergence |
| W-230 | section5 | COV | F-046 | HOLDS | fixed-mode goal target = stored target_amount; F-046 GP1 resolve_goal_target AGREE |
| W-231 | section5 | COV | F-046 | HOLDS | Paychecks-unit target = mult*net_biweekly; F-046 GP1 |
| W-232 | section5 | COV | F-046 | HOLDS | Months-unit target = mult*(net_biweekly*26/12); F-046 GP1 |
| W-233 | section5 | COV | F-005 | VIOLATED-DEAD | 3 projection lines via get_amortization_breakdown; chart_data_service DEAD (F-005) |
| W-234 | section5 | COV | F-013 | PARTIALLY_HOLDS | rate change re-amortize new payment; C-04; F-013 site 4 rate-change re-amort; A-05 replay+rate-change in code |
| W-235 | section5 | NC | - | - | rate changes before origination ignored; generate_schedule; engine edge not verdicted |
| W-236 | section5 | NC | - | - | payment > principal+interest cap to zero; generate_schedule; engine edge not verdicted |
| W-237 | section5 | NC | - | - | schedule terminates at zero balance; generate_schedule; engine edge not verdicted |
| W-238 | section5 | NC | - | - | payments after zero ignored; generate_schedule; engine edge not verdicted |
| W-239 | section5 | COV | F-023 | HOLDS | recurring transfer end_date auto-set payoff; F-023 path D `loan.py:513-516`, A==D by construction |
| W-240 | section5 | COV | F-018 | VIOLATED | Paid Off badge when real principal zero; F-018 names `savings_dashboard_service.py:471,488` RAW-replay paid-off as DEFINITION divergence |
| W-241 | section5 | COV | F-016 | UNKNOWN(Q-11) | refinance new principal = current_real + closing; F-016 P4 `loan.py:1095` PRIMARY-PATH UNKNOWN |
| W-242 | section5 | COV | F-022 | PARTIALLY_HOLDS | break-even = closing/savings; F-022 path C formula correct; render-slot-reuse DEFINITION fork |
| W-243 | section5 | NC | - | - | avalanche priority highest rate; debt_strategy_service.calculate_strategy; strategy ordering, F-013/F-020 audit payment/interest not the ordering |
| W-244 | section5 | NC | - | - | snowball priority smallest balance; debt_strategy_service.py; strategy ordering, no finding |
| W-245 | section5 | NC | - | - | freed payment to extra pool on payoff; debt_strategy_service.py; strategy mechanic, no finding |
| W-246 | section5 | COV | F-025 | HOLDS | total monthly debt = P&I + escrow all loans; F-025 `_compute_debt_summary` monthly_total |
| W-247 | section5 | COV | F-032 | PARTIALLY_HOLDS | DTI = total/gross_monthly, gross=biweekly*26/12; F-025 DTI formula AGREE but F-032 gross denominator off-engine DIVERGE |
| W-248 | section5 | NC | - | - | weighted avg rate = sum(rate*principal)/sum(principal); _compute_debt_summary; weighted-avg-rate not verdicted by F-025 (DTI only) |
| W-249 | section5 | COV | F-046 | HOLDS | months to goal = ceil((target-balance)/monthly); F-046 GP1 calculate_trajectory ROUND_CEILING |
| W-250 | section5 | COV | F-046 | HOLDS | pace ahead/on_track/behind; F-046 GP1 calculate_trajectory |
| W-251 | section5 | NC | - | - | monthly equivalents pattern normalization; obligations.py; obligations.summary, Q-12 territory but no F-NN audits it (A-12 proposed pending) |
| W-252 | section5 | NC | - | - | net monthly committed = income - outflows; obligations.py; obligations.summary, no F-NN |
| W-253 | section5a | COV | F-027 | HOLDS | effective_amount actual-when-populated else estimated; F-027 canonical 4-tier `transaction.py:221-245`, property AGREE |
| W-254 | section5a | COV | F-010 | HOLDS | _sum_remaining uses effective_amount; F-010 income+=effective_amount AGREE |
| W-255 | section5a | COV | F-010 | HOLDS | _sum_all uses effective_amount; F-010 byte-identical to _sum_remaining AGREE |
| W-256 | section5a | COV | F-017 | HOLDS | calc_balances_with_amortization uses effective_amount to detect payment; F-017 path B `:270` |
| W-257 | section5a | COV | F-027 | HOLDS | projected end reflects actual when populated; F-027 tier-3 `actual_amount is not None` |
| W-258 | section5a | COV | F-027 | HOLDS | zero actual overrides estimated; F-027 tier-3 `is not None` (comment `:242-244` actual=0 valid) |
| W-259 | section5a | COV | F-004 | UNKNOWN(Q-10) | grid subtotals use effective_amount; F-004 D1 `grid.py:274` raw effective_amount, subtotal canonicalization Q-10 |
| W-260 | section5a | COV | F-027 | HOLDS | deleted contribute zero; F-027 tier-1 is_deleted->0 |
| W-261 | section5a | COV | F-011 | HOLDS | Credit/Cancelled contribute zero; F-011 tier-2 excludes_from_balance AGREE |
| W-262 | section5a | NC | - | - | templates with paid/settled history archive not hard-delete; templates.hard_delete_template; deletion rule, no finding |
| W-263 | section5a | NC | - | - | template hard delete removes Projected; templates.hard_delete_template; deletion mechanic, no finding |
| W-264 | section5a | NC | - | - | transfer template paid/settled archive not delete; transfers.hard_delete_transfer_template; deletion rule, no finding |
| W-265 | section5a | NC | - | - | transfer template hard delete preserve shadow invariants; transfers.hard_delete; Invariant 2 on delete, F-029/F-031 audit Invariant 3/5 not delete-orphan |
| W-266 | section5a | NC | - | - | account delete blocked if history; accounts.hard_delete_account; deletion rule, no finding |
| W-267 | section5a | NC | - | - | account hard delete cascades params; accounts.hard_delete_account; deletion mechanic, no finding |
| W-268 | section5a | NC | - | - | account hard delete blocked if active templates; accounts.hard_delete_account; deletion rule, no finding |
| W-269 | section5a | NC | - | - | category hard delete fails if in use; categories.delete_category; deletion rule, no finding |
| W-270 | section5a | NC | - | - | archived categories excluded from Add modal; grid route; UI dropdown, no finding |
| W-271 | section5a | NC | - | - | txns with archived categories still render; grid route; UI render, no finding |
| W-272 | section8 | COV | F-005 | VIOLATED-DEAD | X-axis labels include year multi-year; chart_data_service._format_period_label DEAD (F-005) |
| W-273 | section8 | NC | - | - | due_date populated from recurrence day clamped; recurrence_engine.generate; recurrence mechanic, no F-NN audits recurrence_engine |
| W-274 | section8 | NC | - | - | paid_at set on Done/Received cleared on revert; transactions.mark_done/update; paid_at lifecycle, F-027 R4/R5 cover actual_amount write not paid_at |
| W-275 | section8 | COV | F-001 | HOLDS | balance uses pay_period_id exclusively not due_date; F-012 substrate groups by `pay_period_id (:62-64)` |
| W-276 | section8 | NC | - | - | monthly attribution due_date else pay_period start; calendar_service.get_month_detail; calendar attribution, no F-NN |
| W-277 | section8 | NC | - | - | month-end balance = last pay period projected end; calendar_service.get_month_detail; calendar balance, F-003 paths don't include calendar_service |
| W-278 | section8 | NC | - | - | large flag if effective_amount>threshold; calendar_service._get_display_day; large-flag effective_amount read not in F-027 table |
| W-279 | section8 | NC | - | - | 3rd-paycheck-month detection; calendar_service._detect_third_paycheck_months; calendar logic, no F-NN |
| W-280 | section8 | COV | F-027 | HOLDS | variance = actual-estimated settled, 0 projected; F-027 S10 budget_variance_service EQUIVALENT (query-guarded) |
| W-281 | section8 | NC | - | - | txn attributed due_date month else pay_period; budget_variance_service._get_transactions_for_window; attribution window, no F-NN |
| W-282 | section8 | NC | - | - | variance pct = variance/estimated*100; budget_variance_service.compute_variance; variance pct not verdicted by F-027 (S10 was the actual base only) |
| W-283 | section8 | COV | F-042 | HOLDS | spending trends linear regression; F-042 G3 spending_trend single producer AGREE |
| W-284 | section8 | COV | F-042 | HOLDS | pct change = (last-first)/first; F-042 G3 `_safe_pct_change:481-482` exact formula |
| W-285 | section8 | COV | F-042 | HOLDS | flag if abs(pct)>threshold; F-042 G3 compute_trends flagging |
| W-286 | section8 | COV | F-048 | HOLDS | cash runway = balance/avg_daily, avg=paid_30d/30; F-048 `_compute_cash_runway` exact AGREE |
| W-287 | section8 | COV | F-048 | HOLDS | current balance = balance calc current period or anchor; F-048 `_get_balance_info` == dashboard checking_balance |
| W-288 | section8 | NC | - | - | upcoming bills unpaid expense remainder+next; dashboard_service._get_upcoming_bills; bill list (not a money calc), no F-NN |
| W-289 | section8 | COV | F-046 | HOLDS | savings goal completion = balance/target*100; F-046 GP1 (consumers delegate to savings_goal_service) |
| W-290 | section8 | COV | F-004 | HOLDS | spending comparison sum effective_amount paid expense, delta; F-004 D3 `_sum_settled_expenses` producer matches the claim (D1-D3 divergence is a definitional separation, not a W-290 violation) |
| W-291 | section8 | COV | F-032 | PARTIALLY_HOLDS | gross wages = sum gross_biweekly year; year-end sum of canonical engine output HOLDS by construction; broader paycheck_gross has the F-032 off-engine DIVERGE |
| W-292 | section8 | COV | F-033 | HOLDS | net pay total = sum net_pay year; F-033 paycheck_net AGREE, year-end sum of canonical |
| W-293 | section8 | COV | F-018 | HOLDS | mortgage interest = sum interest rows in year; C-05 resolved A-06 (both layers); F-018 A-B AGREE by construction |
| W-294 | section8 | COV | F-056 | HOLDS | spending by category sum effective_amount paid/settled grouped; F-056 entry_sum_total / F-027 S7 AGREE |
| W-295 | section8 | COV | F-053 | HOLDS | debt principal paid = jan1-dec31; F-053 year_summary_principal_paid AGREE-by-construction |
| W-296 | section8 | COV | F-027 | HOLDS | savings contributions = sum shadow income year; F-027 S18 shadow-income sum EQUIVALENT/contract-safe |
| W-297 | section8 | COV | F-025 | HOLDS | DTI escrow included PITI; F-025 `_compute_debt_summary` PITI AGREE (W-297 named in F-025 cross-link) |
| W-298 | test_remediation | COV | F-001 | HOLDS | 52-period balance = anchor + cumulative net excl cancelled/credit; F-001/F-010/F-011 balance engine + tier-2 exclusion |
| W-299 | test_remediation | COV | F-001 | HOLDS | negative anchor + income/expenses arithmetic; F-001/F-010 balance roll-forward |
| W-300 | test_remediation | DUP-0.3 | A-01 | xref | interest portion quantize 0.01 ROUND_HALF_UP; literal A-01 restatement; xref F-017 (`balance_calculator.py:274-276` HALF_UP) |
| W-301 | test_remediation | DUP-0.3 | A-01 | xref | HYSA daily-compound balance quantize 0.01 ROUND_HALF_UP; literal A-01 restatement; xref F-041 (`interest_projection.py:114` HALF_UP) |
| W-302 | test_remediation | COV | F-037 | PARTIALLY_HOLDS | SS tax stops after wage base; F-037 bracket path enforces cap (HOLDS) but calibration path bypasses (DIVERGE) |
| W-303 | test_remediation | NC | - | - | recurrence interval_n=0 guard; recurrence_engine.py; recurrence guard, no F-NN audits recurrence_engine |
| W-304 | transfer_rework_design | COV | F-029 | HOLDS | one-time transfers appear in grid affect balances; F-029 transfer_amount shadows in balance AGREE |
| W-305 | transfer_rework_design | NC | - | - | every transaction non-null account_id; transactions model; NOT NULL FK schema, Phase 1/4 not Phase-3 |
| W-306 | transfer_rework_design | COV | F-029 | HOLDS | shadows auto-deleted on transfer delete CASCADE; F-029 Invariant 2 / sole-mutator AGREE |
| W-307 | transfer_rework_design | COV | F-029 | HOLDS | expense shadow inherits transfer category_id; F-029 create_transfer construction |
| W-308 | transfer_rework_design | COV | F-029 | HOLDS | income shadow default Transfers:Incoming; F-029 create_transfer |
| W-309 | transfer_rework_design | COV | F-029 | HOLDS | both shadows atomic with transfer; F-029 Invariant 1/2 |
| W-310 | transfer_rework_design | COV | F-029 | HOLDS | expense shadow account_id=from; F-029 create_transfer |
| W-311 | transfer_rework_design | COV | F-029 | HOLDS | income shadow account_id=to; F-029 create_transfer |
| W-312 | transfer_rework_design | COV | F-029 | HOLDS | both shadows transfer_id set; F-029 create_transfer |
| W-313 | transfer_rework_design | COV | F-029 | HOLDS | shadows template_id NULL; F-029 create_transfer |
| W-314 | transfer_rework_design | COV | F-029 | HOLDS | amount change updates both shadows; F-029 Invariant 3 update_transfer |
| W-315 | transfer_rework_design | COV | F-029 | HOLDS | status change updates both; F-029 Invariant 4 |
| W-316 | transfer_rework_design | COV | F-029 | HOLDS | pay_period change updates both; F-029 Invariant 5 |
| W-317 | transfer_rework_design | COV | F-029 | HOLDS | category change only expense shadow; F-029 update_transfer |
| W-318 | transfer_rework_design | COV | F-029 | HOLDS | every transfer exactly two shadows expense+income; F-029 Invariant 1 |
| W-319 | transfer_rework_design | COV | F-029 | HOLDS | both shadow estimated_amount = transfer.amount; F-029 Invariant 3 substrate exactly |
| W-320 | transfer_rework_design | COV | F-029 | HOLDS | shadow statuses = parent; F-029 Invariant 4 |
| W-321 | transfer_rework_design | COV | F-029 | HOLDS | both shadows same pay_period_id; F-029 Invariant 5 |
| W-322 | transfer_rework_design | COV | F-031 | HOLDS | balance calc query only budget.transactions (Inv 7); F-031/F-012 Invariant 5 AGREE, zero violation |
| W-323 | transfer_rework_design | COV | F-031 | HOLDS | transfer recurrence calls transfer_service.create_transfer; F-031 transfer_recurrence LEGITIMATE |
| W-324 | transfer_rework_design | COV | F-031 | HOLDS | recurrence passes template category_id; F-031 transfer_recurrence mgmt |
| W-325 | transfer_rework_design | COV | F-031 | HOLDS | balance calc remove transfer query only transactions; F-031/F-012 Invariant 5 AGREE |
| W-326 | transfer_rework_design | COV | F-005 | VIOLATED-DEAD | chart data not filter transfer_id IS NOT NULL; chart_data_service DEAD (F-005) |
| W-327 | transfer_rework_design | COV | F-031 | HOLDS | quick edit detect transfer_id route via transfer_service; F-031 routes/transactions transfer-detect LEGITIMATE |
| W-328 | transfer_rework_design | COV | F-031 | HOLDS | full edit detect transfer_id return transfer form; F-031 `routes/transactions.py:282` resolve parent transfer |
| W-329 | transfer_rework_design | NC | - | - | shadows not marked credit; transaction routes/credit_workflow; shadow-credit prohibition, F-027 S14 is credit_workflow actual/estimated not this rule |
| W-330 | transfer_rework_design | DUP-0.3 | A-07 | xref | carry forward partition regular/shadow; A-07 resolved the carry-forward partition (Read `carry_forward_service.py:272-278`) -- the resolved answer |
| W-331 | transfer_rework_design | COV | F-031 | HOLDS | carry forward dedup shadows by transfer_id call update_transfer once; F-031 `routes/transfers.py:985-994` dedup LEGITIMATE |
| W-332 | transfer_rework_impl | NC | - | - | every transaction non-null account_id; Transaction model; NOT NULL schema, Phase 1/4 |
| W-333 | transfer_rework_impl | NC | - | - | template-gen account_id=template.account_id; recurrence_engine/transactions; creation wiring, no finding |
| W-334 | transfer_rework_impl | NC | - | - | credit payback account_id=original; credit_workflow.py; creation wiring, F-027 S14 is actual/estimated not account_id |
| W-335 | transfer_rework_impl | COV | F-029 | HOLDS | transfer_id NULL regular non-NULL shadow; F-029 shadow identification |
| W-336 | transfer_rework_impl | COV | F-029 | HOLDS | every active transfer two shadows; F-029 Invariant 1 |
| W-337 | transfer_rework_impl | COV | F-029 | HOLDS | no orphaned shadows create/delete both; F-029 Invariant 2 |
| W-338 | transfer_rework_impl | COV | F-029 | HOLDS | both shadows estimated=amount actual match; F-029 Invariant 3 substrate |
| W-339 | transfer_rework_impl | COV | F-029 | HOLDS | shadows status_id = transfer; F-029 Invariant 4 |
| W-340 | transfer_rework_impl | COV | F-029 | HOLDS | shadows pay_period_id = transfer; F-029 Invariant 5 |
| W-341 | transfer_rework_impl | COV | F-029 | HOLDS | category NULL: expense NULL income Transfers:Incoming; F-029 create_transfer |
| W-342 | transfer_rework_impl | NC | - | - | zero/negative amount rejected; transfer_service.create_transfer; input validation, F-029 audits amount consistency not validation |
| W-343 | transfer_rework_impl | NC | - | - | from==to rejected; transfer_service.create_transfer; input validation, no finding |
| W-344 | transfer_rework_impl | NC | - | - | carried transfer is_override on transfer+both shadows; carry_forward_service.py; carry-forward is_override mechanic, A-07 principle but branch impl not verdicted |
| W-345 | transfer_rework_impl | NC | - | - | carried transfer both shadows move atomically; carry_forward_service.py; carry-forward mechanic, no finding |
| W-346 | transfer_rework_impl | COV | F-031 | HOLDS | balance calc only Transaction incl shadows; F-031/F-012 Invariant 5 AGREE |
| W-347 | transfer_rework_impl | COV | F-001 | HOLDS | income shadow + / expense shadow - balance; F-001/F-012 engine is_income/is_expense, shadows ordinary txns |
| W-348 | transfer_rework_impl | COV | F-017 | HOLDS | payment detection income transfer_id in loan; F-017 path B `balance_calculator.py:268-269` |
| W-349 | transfer_rework_impl | COV | F-031 | HOLDS | no transfer counted twice old path removed; F-031/F-012 Invariant 5 zero double-count |
| W-350 | transfer_rework_impl | COV | F-001 | HOLDS | shadow done excluded from projected balance; F-001/F-011 Projected-only gate |
| W-351 | transfer_rework_impl | COV | F-011 | HOLDS | cancelled shadow effective_amount zero excluded; F-011/F-027 tier-2 Cancelled |
| W-352 | transfer_rework_impl | COV | F-029 | HOLDS | shadow creation exactly two; F-029 Invariant 1 [test] |
| W-353 | transfer_rework_impl | COV | F-029 | HOLDS | one expense from / one income to; F-029 create_transfer [test] |
| W-354 | transfer_rework_impl | COV | F-029 | HOLDS | both shadows estimated=amount status match; F-029 Invariant 3/4 [test] |
| W-355 | transfer_rework_impl | COV | F-031 | HOLDS | shadow-path == old transfer-path balances; F-031 confirms only shadow path (old path absent) AGREE [test] |
| W-356 | transfer_rework_impl | COV | F-017 | HOLDS | amortization detect shadow income allocate principal/interest; F-017 path B detection [test] |
| W-357 | year_end_fixes | COV | F-008 | UNKNOWN(Q-15) | Debt Progress balances match loan-page schedule; F-008/F-016 stored-vs-schedule cross-page, Q-15 |
| W-358 | year_end_fixes | COV | F-018 | HOLDS | biweekly split use amortization engine not naive; F-017/F-018 A-06-prepared schedule confirmed |
| W-359 | year_end_fixes | COV | F-014 | PARTIALLY_HOLDS | payment history replayed origination->schedule with confirmed; C-04/A-05; F-014 fixed replay impl, ARM stored (A-04) |
| W-360 | year_end_fixes | COV | F-018 | HOLDS | escrow subtracted from shadow before amortization; F-018/F-019 A-06 `loan_payment_service.py:305-319` |
| W-361 | year_end_fixes | COV | F-018 | HOLDS | biweekly same-month payments redistributed; F-018 A-06 `:321-353` |
| W-362 | year_end_fixes | COV | F-018 | HOLDS | mortgage interest use prepared payment history; C-05 resolved A-06; F-018 A-B AGREE by construction |
| W-363 | year_end_fixes | COV | F-053 | HOLDS | principal paid = jan1-dec31 amortization; F-053 AGREE-by-construction |
| W-364 | year_end_fixes | COV | F-013 | PARTIALLY_HOLDS | contractual P&I ARM re-amortize current balance/rate; F-013 site 11 `loan_payment_service.compute_contractual_pi` ARM formula; A-05 cross-site |
| W-365 | year_end_fixes | COV | F-019 | HOLDS | payments below contractual P&I not adjusted in escrow subtraction; F-019 path B `min(monthly_escrow, p.amount - contractual_pi)` guard |
| W-366 | year_end_fixes | COV | F-006 | UNKNOWN(Q-15) | net worth use amortization-based debt balances; F-006 loan branch schedule, Q-15 |
| W-367 | year_end_fixes | COV | F-055 | PARTIALLY_HOLDS | Savings Progress account for employer contributions; F-055 sums (capped) employer -- accounted, but inherits F-043 card-vs-total DIVERGE |
| W-368 | year_end_fixes | COV | F-054 | UNKNOWN(Q-15) | Savings Progress assumed return via growth engine; F-054 YG1 `_project_investment_for_year`, Q-15 |
| W-369 | year_end_fixes | COV | F-054 | HOLDS | interest accounts include accrued interest Dec31; F-054 YG2 `_compute_interest_for_year` AGREE-by-construction |
| W-370 | year_end_fixes | COV | F-054 | UNKNOWN(Q-15) | investment Dec31 = employee+employer+growth; F-054 YG1 / F-052 W-159, Q-15 |
| W-371 | year_end_fixes | COV | F-007 | UNKNOWN(Q-15) | Savings Progress same growth engine/paths as savings dashboard; F-007/F-054 W-159/W-371 equality not enforced, Q-15 |
| W-372 | year_end_fixes | COV | F-055 | PARTIALLY_HOLDS | employer_contributions field = sum employer all periods; F-055 sums capped employer, inherits F-043 |
| W-373 | year_end_fixes | COV | F-054 | UNKNOWN(Q-15) | investment_growth field = sum growth+interest; F-054 YG1 Q-15 (YG2 interest AGREE-by-construction) |
| W-374 | year_end_fixes | COV | F-045 | HOLDS | total contributions = sum shadow income only; F-045/F-027 S18 shadow-income sum EQUIVALENT |
| W-375 | year_end_fixes | COV | F-041 | HOLDS | HYSA Dec31 > Jan1+transfers by interest; F-041/F-054 YG2 `_compute_interest_for_year` AGREE-by-construction |

### Bin counts (reconcile to 375)

| Bin | Count | Detail |
| --- | --- | --- |
| COVERED | 180 | by inherited verdict: HOLDS 120, PARTIALLY_HOLDS 18, VIOLATED 3, UNKNOWN 31, VIOLATED-DEAD 8 |
| SUPERSEDED | 24 | all `envelope_view` W-127..W-150 (A-02; W-133/134/147 also A-03/C-02) |
| NEEDS-COMPARISON | 161 | grouped by plan-cluster below |
| DUPLICATE-OF-0.3 | 10 | W-118/119/120 (A-07/A-03), W-187 (A-01), W-192 (A-07), W-205/206 (E-01), W-300/301 (A-01), W-330 (A-07) |
| **Total** | **375** | 180 + 24 + 161 + 10 = **375** -- reconciled |

COVERED inherited-verdict reconciliation: HOLDS 120 + PARTIALLY_HOLDS 18 + VIOLATED 3 (W-006, W-216,
W-240) + UNKNOWN 31 (Q-15 x26: net_worth_amort 18 + W-058/W-217/W-357/W-366/
W-368/W-370/W-371/W-373; Q-11 x2: W-057/W-241; Q-10 x3: W-105/W-259 +...; recount: Q-10 W-105/W-259
= 2, Q-11 W-057/W-241 = 2, Q-15 = 27) + VIOLATED-DEAD 8 (W-011/012/035/036/188/ 233/272/326) = 180.
(UNKNOWN sub-split: the governing Q is named per row; the 31 total is the load-bearing count.)

SUPERSEDED guard satisfied: **every one of the 24 `envelope_view` entries (W-127..W-150) is binned
SUPERSEDED with A-02 cited; ZERO is binned VIOLATED.** W-133/W-134/W-147 additionally cite
A-03/C-02. No other plan is wholly superseded: `carry_fwd_design`/`carry_fwd_impl` (Option F) is the
C-01 **winning** side per A-02 (NEEDS-COMPARISON, not VIOLATED, not SUPERSEDED); `section5`'s
engine-replay is "also still in the code" per A-05 (not superseded); `arm_anchor` is the C-04
winning side. Zero superseded-plan entry mis-binned VIOLATED.

### NEEDS-COMPARISON residual (161) grouped by plan-cluster + recommended sub-session split

| Plan | NC count |
| --- | --- |
| account_param_arch | 16 |
| account_params | 17 |
| arm_anchor | 5 |
| calendar_totals | 21 |
| carry_fwd_design | 17 |
| carry_fwd_impl | 18 |
| net_worth_amort | 1 |
| phase8_hardening | 10 |
| phase8b | 3 |
| prod_impl | 2 |
| prod_readiness_v1 | 9 |
| req_v3_addendum | 2 |
| section5 | 11 |
| section5a | 10 |
| section8 | 9 |
| test_remediation | 1 |
| transfer_rework_design | 2 |
| transfer_rework_impl | 7 |
| **Total** | **161** |

Recommended comparison sub-session split (one session per large cluster; small residuals batched;
each prompt scoped to the listed W-NN ranges):

| Sub-session | Plan-clusters | W-NN | Residual |
| --- | --- | --- | --- |
| **P3-cmp-1** account-parameter dispatch/schema | account_param_arch (16) + account_params (17) + arm_anchor dispatch/threading (5: W-050,051,055,056,061) | W-001..041 (the 33 NC) + W-050/051/055/056/061 | 38 |
| **P3-cmp-2** carry-forward (Option F current per A-02; settle-and-roll mechanics) | carry_fwd_design (17) + carry_fwd_impl (18) | W-085..090,094..104 + W-106..117,121..126 | 35 |
| **P3-cmp-3** calendar / analytics-window / deletion | calendar_totals (21) + section8 calendar/variance (9: W-273,274,276..279,281,282,288) + section5a deletion/archive (10: W-262..271) | W-064..084 + listed | 40 |
| **P3-cmp-4** loan/strategy + transfer-shape residual | section5 (11: W-219,235..238,243..245,248,251,252) + transfer_rework_impl (7: W-332,333,334,342..345) + transfer_rework_design (2: W-305,329) | listed | 20 |
| **P3-cmp-5** ops/audit/config/misc (likely out-of-financial-calc-scope -- fast classify) | phase8_hardening (10) + phase8b (3) + prod_impl (2: W-183,185) + prod_readiness_v1 (9) + net_worth_amort (1: W-169) + req_v3_addendum (2: W-207,215) + test_remediation (1: W-303) | W-170..182, W-183/185, W-186/189/190/191/193/194/195/196/197, W-169, W-207/215, W-303 | 28 |
| | | **Total** | **161** |

(P3-cmp-5 is sized small because most of its rows -- audit-log infra, structured logging,
DevConfig/seed_user scripts, IDOR user-scoping -- are outside the audit-plan section-0 scope of
"calculations of money"; the sub-session is expected to classify them out-of-scope/HOLDS-ops quickly
rather than perform deep code comparison.)

### P3-watchlist-triage verification (a-d)

- **(a) Triage table has exactly 375 rows; bin counts sum to 375; reconciliation stated.**
  W-001..W-375 each appears exactly once. COVERED 180 + SUPERSEDED 24 + NEEDS-COMPARISON 161
  - DUPLICATE-OF-0.3 10 = **375**. Per-plan sums verified against priors-0.4 per-plan counts
  (account_param_arch 22 = COV6+NC16; account_params 19 = COV2+NC17; arm_anchor 22 = COV17+NC5;
  calendar_totals 21 = NC21; carry_fwd_design 20 = COV3+NC17; carry_fwd_impl 22 = COV1+DUP3+NC18;
  envelope_view 24 = SUP24; net_worth_amort 19 = COV18+NC1; phase8_hardening 10 = NC10; phase8b 3 =
  NC3; prod_impl 3 = COV1+NC2; prod_readiness_v1 12 = COV1+DUP2+NC9; req_v3_addendum 20 =
  COV16+DUP2+NC2; section5 35 = COV24+NC11; section5a 19 = COV9+NC10; section8 26 = COV17+NC9;
  test_remediation 6 = COV3+DUP2+NC1; transfer_rework_design 28 = COV25+DUP1+NC2; transfer_rework_impl
  25 = COV18+NC7; year_end_fixes 19 = COV19). All 20 plan totals reconcile. **HOLDS.**
- **(b) Every `envelope_view` entry SUPERSEDED, zero mis-binned VIOLATED.** W-127..W-150
  (24/24) binned SUPERSEDED with A-02 cited (W-133/134/147 also A-03/C-02). Grep of the
  triage table for `envelope_view` rows confirms 24 rows, all bin=SUP, none VIOLATED/COV.
  No other A-02..A-07/C-NN-superseded plan exists (Option F / arm_anchor / section5-replay
  are winning sides). **HOLDS.**
- **(c) Self spot-check -- 12 random COVERED rows re-checked against the cited F-NN
  (genuine code-location coverage + correct verdict-inheritance mapping):**
  1. **W-004 -> F-041 HOLDS.** F-041 substrate cites `interest_projection.py:49-114` with
     signature `calculate_interest(balance, apy, compounding, period_start, period_end)`;
     W-004 claims exactly that 5-arg signature; F-041 AGREE -> HOLDS. **PASS.**
  2. **W-048 -> F-013 PARTIALLY_HOLDS.** F-013 explicitly names W-048 ("PLAN_DRIFT against
     W-048 ... A-05 ARM-method") and shows the ARM formula holds at the 8 ARM sites while
     the 16-site concept diverges; DIVERGE -> PARTIALLY_HOLDS (claim holds in part).
     **PASS.**
  3. **W-152 -> F-006 UNKNOWN(Q-15).** F-006 verbatim: "net_worth_amort W-152 ('identical
     calculation paths') is planned-per-plan. PLAN.", verdict UNKNOWN blocked Q-15;
     UNKNOWN -> UNKNOWN(Q-15). **PASS.**
  4. **W-201 -> F-019 HOLDS.** F-019 escrow_per_period AGREE; "monthly escrow = sum
     active component annual/12" is `escrow_calculator.py:54-55`; W-201 claims that;
     AGREE -> HOLDS. **PASS.**
  5. **W-240 -> F-018 VIOLATED.** F-018 names `savings_dashboard_service.py:471,488`
     (`_check_loan_paid_off` RAW replay) as a DEFINITION divergence; W-240 (Paid-Off badge
     via that paid-off check) inherits the DIVERGE -> VIOLATED. **PASS.**
  6. **W-293 -> F-018 HOLDS.** C-05 resolved by A-06 (both layers apply); F-018 Path B
     (`_compute_mortgage_interest`) consumes A-06-prepared schedule, A-B AGREE by
     construction; W-293's section8 rule holds with the A-06 schedule -> HOLDS. **PASS.**
  7. **W-319 -> F-029 HOLDS.** F-029 substrate: `create_transfer` builds both shadows with
     `estimated_amount=amount` (Invariant 3); W-319 claims exactly that; F-029 AGREE ->
     HOLDS. **PASS.**
  8. **W-348 -> F-017 HOLDS.** F-017 Path B `balance_calculator.py:262-270` gated
     `txn.transfer_id is not None and txn.is_income` -- the loan-payment detection W-348
     claims; the detection mechanism is confirmed (F-017's DIVERGE is the interest base/A-06,
     not the detection) -> HOLDS for the detection claim. **PASS.**
  9. **W-046 -> F-014 HOLDS.** F-014 Path A `amortization_engine.py:977-984` is the ARM
     anchor / fixed schedule-walk dual policy; F-014 states "A-04 makes the ARM-stored /
     fixed-walked split INTENDED"; W-046 claims that split -> HOLDS. **PASS.**
  10. **W-286 -> F-048 HOLDS.** F-048 `_compute_cash_runway@dashboard_service.py:375-417`
      = `balance / (paid_30d/30)`, single-path AGREE; W-286 claims exactly that formula ->
      HOLDS. **PASS.**
  11. **W-105 -> F-004 UNKNOWN(Q-10).** F-004 D1-D2 same-page subtotal-vs-balance is the
      "identical totals across cell/subtotal/balance" question; F-004 verdict UNKNOWN
      blocked Q-10 -> UNKNOWN(Q-10). **PASS.**
  12. **W-375 -> F-041 HOLDS.** F-041/F-054 YG2 `_compute_interest_for_year` sums the single
      apy_interest engine output AGREE-by-construction; W-375 (HYSA Dec31 > Jan1+transfers
      by interest) inherits AGREE -> HOLDS. **PASS.**
  **Spot-check pass rate: 12/12.** The inheritance mapping is reliable (no 2+ failures; no
  stop-condition triggered).
- **(d) NEEDS-COMPARISON residual grouped + concrete sub-session split with per-session
  counts.** 161 grouped by 18 plans (table above), split into 5 sub-sessions P3-cmp-1..5
  with per-session residuals 38 / 35 / 40 / 20 / 28 = 161; each scoped to explicit W-NN
  ranges. **HOLDS.**

P3-watchlist-triage complete (375 W-NN binned; bins reconcile to 375; envelope_view SUPERSEDED guard
satisfied; 12/12 COVERED spot-check; NEEDS-COMPARISON residual scoped into 5 sub-sessions). Phase 3
is **NOT** complete -- the 5 NEEDS-COMPARISON sub-sessions (P3-cmp-1..5) and **P3-reconcile** (the
Phase-3 completion gate) remain; P3-reconcile is the only session that may declare Phase 3 complete.
P3-a..P3-d2 / P2 / P1 / priors content unmodified (append-only; this section adds no F-NN finding
and does not touch the `Finding IDs used` header). No source, test, or migration file modified. Not
committed; developer reviews between sessions.

#### P3-cmp-1 verdicts: account-parameter dispatch/schema

Session P3-cmp-1, 2026-05-16. This IS a fresh-code-read session (unlike the triage): for each of the
38 W-NN the code that should embody the claim was located, Read in full at source THIS session, and
a verdict assigned. Append-only; F-001..F-056, the `Finding IDs used` header, and the triage table
above are untouched. No source/test/ migration modified.

**Triage residual reconciliation.** Cluster definition (`03_consistency.md:4529`): P3-cmp-1 =
account_param_arch (16 NC) + account_params (17 NC) + arm_anchor dispatch/ threading (5:
W-050,051,055,056,061). The 38 W-NN, enumerated:

- account_param_arch NC (16): W-001, W-002, W-003, W-005, W-008, W-010, W-013, W-014,
  W-015, W-016, W-017, W-018, W-019, W-020, W-021, W-022.
- account_params NC (17): W-023, W-024, W-025, W-026, W-027, W-028, W-029, W-030, W-031,
  W-032, W-033, W-034, W-037, W-038, W-039, W-040, W-041.
- arm_anchor dispatch/threading (5): W-050, W-051, W-055, W-056, W-061.

**Triage residual for this cluster: 38; rows produced: 38** (16 + 17 + 5 = 38, reconciled).

**Option-adoption determination (mandated before any Option-conditional verdict).** Grep + full-file
Read this session:

- **Option A ADOPTED (the rename).** No `HysaParams` class exists anywhere
  (`grep -rn` this session: only a docstring mention at `app/models/ref.py:37`). The model
  is `InterestParams` in `app/models/interest_params.py`; the FK-name comment at
  `interest_params.py:46-51` records the migrated lineage `hysa_params_account_id_fkey` ->
  `interest_params_account_id_fkey` (44893a9dbcc3). The table was renamed to
  `interest_params` -- Option A's rename. **But** the Option-A CD-support columns are absent
  (see W-019).
- **Option D NOT ADOPTED.** No `category_id`-based investment param dispatch in
  `accounts.py` or the savings dispatch. `accounts.py:345-350` and
  `savings_dashboard_service.py:226-232` use the metadata-flag elimination predicate
  `has_parameters AND NOT has_interest AND NOT has_amortization`, not
  `category_id IN (Retirement, Investment)`. The only `category_id` use in the savings path
  is the display grouping `_group_accounts_by_category@savings_dashboard_service.py:927-936`,
  not param dispatch. -> W-020 is **N/A-OPTION-NOT-ADOPTED**.

**Supersession framing (audit-plan section 3.1: "treat the current plan as the comparand when one
plan supersedes another").** `account_params` (newer) explicitly supersedes `account_param_arch`'s
(older) type-ID / hardcoded-set / `HysaParams` / route-level-frozenset mechanisms with metadata-flag

+ duck-typed dispatch (W-021/W-022/W-023/W-026/W-038...). Where the code implements the
**superseding** mechanism and the financial behavior is correct, the `account_param_arch`
literal-mechanism claim is recorded **PARTIALLY_HOLDS / PLAN_DRIFT-STRUCTURAL** (substance holds;
mechanism superseded; **no wrong number**) -- this mirrors the triage's
PARTIALLY_HOLDS-where-claim-holds-in-part precedent and the envelope_view "code correctly does not
implement a superseded plan" precedent, except these are NC (verdicted by fresh read) not SUP.

| W-NN | plan | claim (one line) | code location Read (file:line) | verdict | classification if drift | evidence (what the code actually does) | cross-ref |
| ---- | ---- | ---- | ---- | ---- | ---- | ---- | ---- |
| W-001 | account_param_arch | HYSA stores apy Numeric(7,5) + compounding String | `interest_params.py:60-63` (full file read) | HOLDS | (note) PLAN_DRIFT-STRUCTURAL (model renamed) | `apy = Numeric(7,5)` `:60`; `compounding_frequency = String(10)` `:61-63` -- exactly the claimed shapes. Model is `InterestParams` not `HysaParams` (Option A rename adopted, `:46-51`); the column-type claim itself holds. No wrong number. | W-019, W-028, F-041 |
| W-002 | account_param_arch | LoanParams tracks orig/current_principal/rate/term/orig_date/payment_day | `loan_params.py:53-58` (full) | HOLDS | -- | `original_principal:53`, `current_principal:54`, `interest_rate:55`, `term_months:56`, `origination_date:57`, `payment_day:58` -- all six present, NOT NULL, CHECK-constrained. | F-014, F-026 |
| W-003 | account_param_arch | Retirement types share InvestmentParams (return, contrib limit, employer cols) | `investment_params.py:80-92` (full) | HOLDS | -- | `assumed_annual_return:80`, `annual_contribution_limit:84`, `employer_contribution_type:86`, `employer_flat/match/match_cap_percentage:90-92` -- all present. (Whether all retirement types share it = W-014/018/037 dispatch.) | F-043 |
| W-005 | account_param_arch | get_loan_projection accepts exactly six attrs | `amortization_engine.py:864-959` (full fn) | HOLDS | -- | Reads exactly `origination_date:909`, `term_months:909`, `original_principal:912`, `current_principal:913`, `interest_rate:914`, `payment_day:935`; `is_arm:916` is the documented OPTIONAL arm_anchor extension (docstring `:897`). The six are exactly consumed. | F-013, W-050 |
| W-008 | account_param_arch | retirement_gap_calculator works off projections + is_traditional; no direct InvestmentParams read | `retirement_gap_calculator.py:7,37-136` (full file) | HOLDS | -- | File imports only logging/dataclass/date/Decimal -- zero model imports; docstring `:7` "All functions are pure (no DB access)"; `calculate_gap` takes `retirement_account_projections` dicts (`projected_balance`,`is_traditional` `:50-52`), uses `is_traditional` `:115`. No InvestmentParams read. | W-029 |
| W-010 | account_param_arch | tax treatment via hardcoded TRADITIONAL_TYPE_ENUMS frozenset at route | `retirement_dashboard_service.py:159-161,496` | PARTIALLY_HOLDS | PLAN_DRIFT-STRUCTURAL | `grep` THIS session: NO `TRADITIONAL_TYPE_ENUMS` anywhere. The frozenset is **metadata-derived** `frozenset(rt.id for rt in retirement_types if rt.is_pretax)` `:159-161` in the SERVICE (not a hardcoded route-level enum). Tax-treatment intent holds; the literal frozenset-at-route mechanism is superseded by account_params is_pretax metadata. No wrong number. | W-026, W-029, W-039 |
| W-013 | account_param_arch | auto-create HysaParams for HYSA + redirect hysa_detail | `accounts.py:338-340,361-364` (create_account `:288-379` full) | PARTIALLY_HOLDS | PLAN_DRIFT-STRUCTURAL | `if account_type.has_interest: add(InterestParams(...))` `:338-340`; redirect `accounts.interest_detail` `:361-364`. HysaParams->InterestParams (Option A), HYSA-type-check->`has_interest` (W-021), hysa_detail->interest_detail. Substance (auto-create + redirect) holds via superseding mechanism. No wrong number. | W-021, W-001 |
| W-014 | account_param_arch | auto-create InvestmentParams for hardcoded retirement/investment set + redirect | `accounts.py:345-350,373-375` (full fn) | PARTIALLY_HOLDS | PLAN_DRIFT-STRUCTURAL | `if has_parameters AND NOT has_interest AND NOT has_amortization: add(InvestmentParams)` `:345-350`; redirect `investment.dashboard` `:373-375`. Hardcoded-set -> metadata-flag elimination (W-022/W-037 superseding). Substance holds. No wrong number. | W-022, W-037 |
| W-015 | account_param_arch | account create redirects to loan setup for has_amortization | `accounts.py:365-368` (full fn) | HOLDS | -- | `elif account_type and account_type.has_amortization: next_url = url_for("loan.dashboard", account_id=..., setup=1)` `:365-368` -- exactly the has_amortization flag, redirect to loan setup. | W-017 |
| W-016 | account_param_arch | savings batch-load HysaParams by acct_type_id == HYSA type ID | `savings_dashboard_service.py:208-216` (`_load_account_params:201-292`) | PARTIALLY_HOLDS | PLAN_DRIFT-STRUCTURAL | Batch-loads `InterestParams` filtered by `account_type.has_interest` `:208-216`, NOT by HYSA acct_type_id; model InterestParams not HysaParams. Type-ID mechanism superseded by metadata flag (W-021). Substance holds. No wrong number. | W-021, F-007 |
| W-017 | account_param_arch | savings batch-load LoanParams by has_amortization flag | `savings_dashboard_service.py:219-221` | HOLDS | -- | `amort_type_ids = {at.id for at in query(AccountType).filter_by(has_amortization=True)}` `:219-221` -- exactly the has_amortization flag, as claimed. | W-015 |
| W-018 | account_param_arch | savings batch-load InvestmentParams by hardcoded type-ID set | `savings_dashboard_service.py:226-232` | PARTIALLY_HOLDS | PLAN_DRIFT-STRUCTURAL | `inv_account_ids` by `has_parameters AND NOT has_interest AND NOT has_amortization` `:226-232` -- metadata-flag elimination, NOT a hardcoded type-ID set. account_param_arch mechanism superseded by W-022/W-037. Substance holds. No wrong number. | W-022, W-037 |
| W-019 | account_param_arch | (Option A) InterestParams has nullable maturity_date + term_months for CD | `interest_params.py` (full file, 73 lines) | VIOLATED | PLAN_DRIFT-STRUCTURAL | Option A's rename WAS adopted (table `interest_params`, `:20`), but the model has ONLY `id,account_id,apy,compounding_frequency` -- **no `maturity_date`, no `term_months`** (full-file read). Option A partially adopted: rename yes, CD-support columns no. No wrong number (no CD account type seeded -- latent missing feature). | W-001, W-021 |
| W-020 | account_param_arch | (Option D) investment dispatch via category_id queries replacing type-ID sets | `accounts.py:345-350`; `savings_dashboard_service.py:226-232` | N/A-OPTION-NOT-ADOPTED | -- | Option D NOT adopted: dispatch uses metadata-flag predicate `has_parameters AND NOT has_interest AND NOT has_amortization` (`accounts.py:345-350`, `savings_dashboard_service.py:226-232`), not `category_id IN (Retirement,Investment)`. `category_id` appears only in display grouping `savings_dashboard_service.py:927-936`. | W-022 |
| W-021 | account_param_arch | (Phase 4) replace HYSA type-ID checks with has_interest flag in accounts/chart_data/savings | `accounts.py:338,361`; `savings_dashboard_service.py:210` | PARTIALLY_HOLDS | PLAN_DRIFT-STRUCTURAL | `accounts.py` uses `has_interest` flag (`:338`,`:361`); savings uses `has_interest` (`savings_dashboard_service.py:210`) -- both HOLD. `chart_data_service.py` was REMOVED in `e3b3a5e` (F-005 grep-proven) -- that third site is VIOLATED-DEAD/moot. Net: live sites adopted the flag; dead site moot. No wrong number. | F-005, W-001, W-013 |
| W-022 | account_param_arch | (Phase 4) replace investment type-ID sets with category-based queries in accounts/savings | `accounts.py:345-350`; `savings_dashboard_service.py:226-232` | PARTIALLY_HOLDS | PLAN_DRIFT-STRUCTURAL | Hardcoded type-ID sets ARE removed (goal met) but via metadata-flag elimination, NOT `category_id` queries as the claim specifies. The dispatch is correct; the mechanism differs from the claim's "category-based queries". No wrong number. | W-020, W-037 |
| W-023 | account_params | interest/loan routing via duck-typed attribute access not type-ID | `balance_calculator.py:141,213,220` (`calculate_balances_with_interest:112-173`, `_with_amortization:176-...`) | HOLDS | -- | `if not interest_params or not hasattr(interest_params,"apy")` `:141`; `if not loan_params or not hasattr(loan_params,"interest_rate")` `:213`; `is_arm = getattr(loan_params,"is_arm",False)` `:220` -- duck-typed `hasattr`/`getattr`, no type-ID dispatch. | W-025, F-041 |
| W-024 | account_params | has_parameters=True types auto-create param rows | `accounts.py:334-350` (full create_account) | HOLDS | (note) plan line-cite stale | Auto-create at `:334-350` (NOT plan's `:137,159,576`): `has_interest`->InterestParams `:338-340`; `has_parameters & !interest & !amort`->InvestmentParams `:345-350`. Loan params deliberately NOT auto-created (W-034) -- has_amortization routed to loan setup `:365-368`. | W-034, W-037 |
| W-025 | account_params | HYSA/MM/CD/HSA use unified interest projection when has_interest | `interest_projection.py:49` (engine; F-041 single-engine) | HOLDS | -- | Single canonical engine `calculate_interest@:49` (F-041 AGREE: every consumer delegates, no re-derivation). has_interest accounts (HYSA/MM/HSA per seeds) route through InterestParams->this engine. "CD" not in `ACCT_TYPE_SEEDS` -- vacuously covered (no CD type exists). | F-041, W-023, W-030 |
| W-026 | account_params | Traditional 401k/IRA pre-tax via metadata flag not enum dispatch | `retirement_dashboard_service.py:159-161,496` | HOLDS | (note) plan line-cite stale | `traditional_type_ids = frozenset(rt.id for rt in retirement_types if rt.is_pretax)` `:159-161` (metadata flag, not enum); `is_traditional = acct.account_type_id in traditional_type_ids` `:496`. Plan cites `retirement.py:51,131-132,332` (stale); actual in retirement_dashboard_service. | W-010, W-029, W-039 |
| W-027 | account_params | is_liquid types contribute to emergency fund + appear in goal dropdowns | `savings_dashboard_service.py:143-145,152-156`; `routes/savings.py:55-60` | HOLDS | (note) dropdown nuance | Emergency fund: `if account_type.is_liquid: total_savings += current_balance` `:143-145` -> `calculate_savings_metrics` `:147-149` -- exact. Dropdown: `_goal_form_context` loads ALL active accounts `:55-60` (is_liquid accounts DO appear; the dropdown is a superset, not is_liquid-restricted -- claim "appear in" is satisfied). No wrong number. | F-047 |
| W-028 | account_params | InterestParams stores APY + compounding for projections | `interest_params.py:60-63` (full) | HOLDS | (note) plan path-cite wrong | `apy:60` + `compounding_frequency:61-63` on `InterestParams`. Plan cites `app/models/loan_params.py` (WRONG file -- interest params live in `interest_params.py`); the model substance holds. | W-001, F-041 |
| W-029 | account_params | is_pretax accounts treated differently in retirement-gap tax calc | `retirement_dashboard_service.py:159-161,496`; `retirement_gap_calculator.py:115-122` | HOLDS | -- | is_pretax -> traditional_type_ids `:159-161` -> `is_traditional` `:496` -> `calculate_gap`: `if is_traditional: traditional_total += bal` then `traditional_total*(1-tax)+roth_total` `:115-122`. Pre-tax balances taxed, Roth not. | W-008, W-026 |
| W-030 | account_params | has_interest default false; true only for HYSA + HSA | `ref.py:142-145`; `ref_seeds.py:41,42,44` | PARTIALLY_HOLDS | PLAN_DRIFT-STRUCTURAL (intra-plan) | Default-false HOLDS (`ref.py:142-145` `default=False, server_default false`). Seed sets has_interest True for HYSA `:41`, **Money Market `:42`**, HSA `:44` -- "only HYSA and HSA" is too narrow; the code's broader set (incl. MM) is CORRECT and matches W-025 ("HYSA, MM, CD, HSA"). Intra-plan W-030-vs-W-025 tension; code follows the correct W-025 set. No wrong number. | W-025, W-031 |
| W-031 | account_params | is_pretax default false; true for 401(k) + Traditional IRA | `ref.py:146-148`; `ref_seeds.py:51,52,53` | HOLDS | -- | Default-false (`ref.py:146-148`). Seed `401(k):51` is_pretax=True, `Traditional IRA:53` True, `Roth 401(k):52` False (column order per `ref_seeds.py:25-26`). Exactly 401(k) + Trad IRA. | W-026, W-030 |
| W-032 | account_params | HSA gains has_parameters=True | `ref_seeds.py:44` | HOLDS | -- | `("HSA","Asset",True,False,True,False,False,...)` `:44` -- has_parameters=True (1st flag), has_interest=True. HSA auto-creates InterestParams via `accounts.py:338`. | W-024, W-025 |
| W-033 | account_params | is_liquid identifies Checking/Savings/HYSA/Money Market | `ref_seeds.py:39-42,44` | HOLDS | -- | is_liquid=True for `Checking:39`, `Savings:40`, `HYSA:41`, `Money Market:42`; `HSA:44` is_liquid=False. Exactly the claimed four. | W-027 |
| W-034 | account_params | auto-create Interest/Investment params but NOT loan params for has_amortization | `accounts.py:337-350` (full create_account) | HOLDS | (note) plan line-cite stale | InterestParams when has_interest `:338-340`; InvestmentParams when has_parameters&!interest&!amort `:345-350`; **zero LoanParams creation anywhere in create_account** (full-fn read `:288-379`) -- loan setup via redirect `:365-368`. Plan cites `:132-173` (stale); actual `:334-350`. | W-024, W-037 |
| W-037 | account_params | investment auto-create only has_parameters & !interest & !amortizing | `accounts.py:345-350` | HOLDS | (note) plan line-cite stale | `if account_type and account_type.has_parameters and not has_interest and not has_amortization: add(InvestmentParams)` `:345-350` -- predicate verbatim. Plan cites `:141-151` (stale); actual `:345-350`. | W-014, W-024, W-034 |
| W-038 | account_params | savings needs_setup unified by metadata flags (interest/amort/default-invest) | `savings_dashboard_service.py:402-409` | HOLDS | (note) plan line-cite stale | `if has_parameters: if has_interest: needs_setup = interest_params is None; elif has_amortization: needs_setup = loan_params is None; else: needs_setup = investment_params is None` `:402-409` -- metadata-flag-unified exactly. Plan cites `routes/savings.py:299-305` (stale); actual in service. | W-024 |
| W-039 | account_params | retirement-gap dispatch includes is_pretax metadata flag | `retirement_dashboard_service.py:159-161,496` | HOLDS | (note) plan line-cite stale | is_pretax included via `traditional_type_ids` derivation `:159-161` and `is_traditional` `:496`. Plan cites `retirement.py:131-332` (stale); actual in retirement_dashboard_service. | W-026, W-029 |
| W-040 | account_params | 529 Plan auto-creates InvestmentParams + displays growth when has_parameters | `ref_seeds.py:56`; `accounts.py:345-350,373-375` | PARTIALLY_HOLDS | PLAN_DRIFT-STRUCTURAL | `529 Plan:56` = has_parameters=True, has_interest=False, has_amortization=False -> matches `accounts.py:345-350` -> auto-creates InvestmentParams (HOLDS); redirect `investment.dashboard` `:373-375` for growth display. Plan's `chart_data_service.py` display half is VIOLATED-DEAD (F-005: file removed `e3b3a5e`), superseded by investment.dashboard. No wrong number. | F-005, W-014, W-037 |
| W-041 | account_params | no calc services modified; only dispatch logic changed | multi-engine (`interest_projection.py:49`, `amortization_engine.py:864`, `balance_calculator.py:141`); F-041/F-042 cross-ref | HOLDS | (note) meta-claim caveat | Observable state consistent: calc engines are single canonical producers (F-041 interest AGREE; F-042 growth substrate; F-013 amortization substrate); dispatch is metadata-flag/duck-typed (W-013/W-014/W-023). The negative "not modified" is a git-history process assertion not falsifiable from code-state (no Q gates it -> not UNKNOWN); recorded HOLDS-as-observable with this caveat. | F-041, F-042, W-023 |
| W-050 | arm_anchor | schedule generated exactly once in get_loan_projection (not twice) | `amortization_engine.py:908-982` (full fn) | HOLDS | -- | Single `generate_schedule(...)` call `:932-942`; summary via `_derive_summary_metrics(schedule,...)` `:945-947`; monthly_payment via `calculate_monthly_payment` formula `:952/:957` (not a 2nd schedule gen). Full-fn read `:864-982`: no second `generate_schedule`/`calculate_summary`. Docstring `:888-891` states the once-not-twice intent. | W-005, F-013 |
| W-051 | arm_anchor | calculate_summary accepts anchor + threads to BOTH generate_schedule calls | `amortization_engine.py:649-751` (full fn) | HOLDS | -- | Signature `anchor_balance=None, anchor_date=None` `:660-661`; standard `generate_schedule` passes `anchor_balance=anchor_balance, anchor_date=anchor_date` `:711-712`; accelerated `generate_schedule` passes same `:729-730`. Both calls threaded. | W-055, W-056 |
| W-055 | arm_anchor | extra_payment mode: calculate_summary passes anchor for ARM | `loan.py:863-913` (payoff_calculate) | HOLDS | -- | `anchor_bal = current_principal if params.is_arm else None` `:893-896`; `mode=="extra_payment"` calls `calculate_summary(..., anchor_balance=anchor_bal, anchor_date=anchor_dt)` `:898-913`. ARM passes anchor, fixed-rate None (correct). | W-051, W-056 |
| W-056 | arm_anchor | committed + accelerated schedules pass anchor for ARM | `loan.py:917-948` (payoff_calculate) | HOLDS | -- | committed `generate_schedule` passes `anchor_balance=anchor_bal,anchor_date=anchor_dt` `:933-934`; accelerated passes same `:946-947`; original (contractual baseline) correctly omits anchor `:917-923` per comment `:892`. | W-051, W-055 |
| W-061 | arm_anchor | ARM schedule balance decreases monotonically from today forward | `amortization_engine.py:561-591` (std path), `:531-560` (neg-am path), `:44` | HOLDS | (note) input-dependent caveat | Post-anchor PROJECTED rows have no payment record -> standard path `:566-591` (`principal_portion = monthly_payment - interest > 0`, `balance -= principal_portion`) -> strictly decreasing by amortization construction; from-today-forward monotonicity holds in the normal case. Caveat: the documented negative-amortization branch (`:531-560`, `:44` "only interest accrues") makes monotonicity INPUT-dependent (a post-anchor projected payment below interest due would raise balance) -- not engine-guaranteed. F-026 (payment-value drift) is a SEPARATE proven symptom and does not break intra-schedule monotonicity. | F-026, W-050 |

**Escalation decision.** The rule escalates only VIOLATED/PARTIALLY_HOLDS that produce a
**wrong financial number**. Walked every non-HOLDS row: W-010/013/014/016/018/021/022/040 are
superseded-mechanism structural drift where the dispatch produces correct numbers; W-019 is a
missing CD-support column with no CD account type seeded (latent, no number); W-020 is
option-not-adopted; W-030 is an intra-plan enumeration narrowing where the code is **more** correct
than the claim (Money Market correctly interest-bearing).
**None produces a wrong financial number.** Per the rule, all stay one-row PLAN_DRIFT-STRUCTURAL --
HIGH/MEDIUM Phase-8 substrate, no CRITICAL sub-finding. This is consistent with the triage framing
that the whole account-param cluster is schema-shape / dispatch-wiring with no Phase-3 consistency
(wrong-number) finding; the dispatch is numerically correct, the drift is the older plan's mechanism
description being superseded by the newer plan the code implements.

**P3-cmp-1 verification (a-f):**

- **(a) 38 rows == triage NEEDS-COMPARISON account-param list.** Triage residual for this
  cluster: 38; rows produced: 38 (account_param_arch 16 + account_params 17 + arm_anchor 5).
  Every W-NN from the `03_consistency.md:4529` cluster definition appears exactly once; the
  enumerated list above reconciles to 38. **PASS.**
- **(b) Every verdict cites code Read this session.** Every row's "code location" was Read
  at source in P3-cmp-1 (models full-file; create_account `:288-379` full; calculate_summary
  `:649-751` full; get_loan_projection `:864-982` full; payoff_calculate `:863-948`;
  retirement_gap_calculator full file; ref_seeds/ref/balance_calculator/savings dispatch
  blocks). No verdict inherited from a plan's "complete-per-plan" self-report. **PASS.**
- **(c) Every wrong-number VIOLATED/PARTIALLY has a worked example.** None of the 1 VIOLATED
  / 9 PARTIALLY_HOLDS produces a wrong financial number (escalation decision above), so no
  worked example is required; the escalation walk is recorded instead. **PASS (vacuous).**
- **(d) Every N/A-OPTION states the adopted option with proof.** W-020:
  N/A-OPTION-NOT-ADOPTED -- Option D not adopted; proof `accounts.py:345-350` +
  `savings_dashboard_service.py:226-232` use the metadata-flag predicate, not `category_id`
  queries. Option A's rename WAS adopted (proof: no `HysaParams` class; `interest_params.py`
  table `:20`, FK-lineage comment `:46-51`) -- bears on W-001/W-013/W-016/W-019. **PASS.**
- **(e) Self-spot-check: 6 random verdicts re-Read.**
  1. **W-002 HOLDS** -- re-read `loan_params.py:53-58`: original_principal/current_principal/
     interest_rate/term_months/origination_date/payment_day all present NOT NULL. Confirmed.
  2. **W-019 VIOLATED** -- re-read `interest_params.py` full (73 lines): columns are
     id/account_id/apy/compounding_frequency only; no maturity_date/term_months. Confirmed.
  3. **W-037 HOLDS** -- re-read `accounts.py:345-350`: predicate `has_parameters and not
     has_interest and not has_amortization` verbatim. Confirmed.
  4. **W-031 HOLDS** -- re-read `ref_seeds.py:51-53` with header `:25-26` column order:
     401(k) is_pretax=True, Roth 401(k) False, Traditional IRA True. Confirmed.
  5. **W-051 HOLDS** -- re-read `amortization_engine.py:660-661,711-712,729-730`: anchor
     params in signature and threaded to both generate_schedule calls. Confirmed.
  6. **W-061 HOLDS(caveat)** -- re-read `amortization_engine.py:531-560` (neg-am branch,
     `principal_portion = total_payment - interest` can be negative -> balance rises) and
     `:566-591` (std path strictly decreasing). The caveat is accurate. Confirmed.
  **Spot-check pass rate: 6/6.** No 2+ failures; stop-condition not triggered.
- **(f) Every UNKNOWN names the blocking Q.** Zero UNKNOWN verdicts in this cluster (all 38
  are dispatch/schema/wiring; the Q-gated arm_anchor entries -- W-057/W-058/W-062 -- were
  COVERED in the triage, not in this NC cluster). **PASS (vacuous).**

P3-cmp-1 complete: 38 account-parameter dispatch/schema verdicts (27 HOLDS, 9 PARTIALLY_HOLDS, 1
VIOLATED, 1 N/A-OPTION-NOT-ADOPTED; 0 UNKNOWN; 0 wrong-number escalations -- all drift is
PLAN_DRIFT-STRUCTURAL). Phase 3 is **NOT** complete -- P3-cmp-2 (carry-forward), P3-cmp-3
(calendar/analytics/deletion), P3-cmp-4 (loan/strategy/transfer), P3-cmp-5 (ops/audit/misc), then
**P3-reconcile** (the only session that may declare Phase 3 complete) remain. F-001..F-056 / triage
table / `Finding IDs used` header unmodified (append-only). No source, test, or migration file
modified. Not committed; developer reviews between sessions.

#### P3-cmp-2 verdicts: carry-forward Option F mechanics

Session P3-cmp-2, 2026-05-16. Fresh-code-read session: for each of the 35 W-NN the code that should
embody the claim was located and Read **in full at source THIS session** -- the entire
`carry_forward_service.py` (1016 lines), `transaction_service.settle_from_entries` (`:38-168`),
`entry_service.compute_actual_from_entries` (`:428-446`), `recurrence_engine.generate_for_template`
(`:54-177`) + `can_generate_in_period` (`:180-248`), `schemas/validation.py:170-257`,
`routes/transactions.py:1071-1183` + `mark_done:585-619`, `routes/templates.py:163-252`,
`templates/grid/_carry_forward_preview_modal.html` (full). Append-only; F-001..F-056, the
`Finding IDs used` header, the triage table, and the P3-cmp-1 subsection are untouched. No
source/test/migration modified.

**Triage residual reconciliation.** Cluster definition (`03_consistency.md:4530`): P3-cmp-2 =
carry_fwd_design (17 NC) + carry_fwd_impl (18 NC). The 35 W-NN, enumerated:

- carry_fwd_design NC (17): W-085, W-086, W-087, W-088, W-089, W-090, W-094, W-095, W-096,
  W-097, W-098, W-099, W-100, W-101, W-102, W-103, W-104.
- carry_fwd_impl NC (18): W-106, W-107, W-108, W-109, W-110, W-111, W-112, W-113, W-114,
  W-115, W-116, W-117, W-121, W-122, W-123, W-124, W-125, W-126.

**Triage residual for this cluster: 35; rows produced: 35** (17 + 18 = 35, reconciled).
W-091/W-092/W-093 (COV F-002/F-004), W-105 (COV F-004 Q-10), W-118/W-119/W-120 (DUP-0.3 A-07/A-03)
are NOT in this cluster -- excluded by design, not missed.

**Comparand framing (mandated asymmetry).** A-02 (RESOLVED, auditor-verified ACCURATE
`09_open_questions.md:96-102`): the data-layer settle (Option F / `carry_fwd_impl`) IS current;
`envelope_view` is superseded and its data model was never built. Option F is the C-01 **winning**
side -- so these are verdicted by fresh read AGAINST THE CURRENT CODE, NOT against `envelope_view`
(which would be backwards). A-03 (is_override blocks regeneration) and A-07 (three-branch partition
transfer/envelope/discrete, with the discrete sub-split template-linked-vs-ad-hoc) are the resolved
partition/skip comparands. **The carry-forward settle-source / roll-leftover / bump-canonical
mechanism IS the money arithmetic** (unlike P3-cmp-1's account-param dispatch, which reaches the
same number via a superseding mechanism). The "PLAN_DRIFT-STRUCTURAL / no wrong number by
supersession" reflex from P3-cmp-1 is therefore explicitly NOT inherited here. Every
settle/roll/bump claim was verified by Reading the arithmetic and computing the dollar result by
hand against the plan's stated behavior. The result: the money math is
**provably exactly the plan's intent at every site** -- the verdicts are HOLDS
*because the arithmetic is correct*, not "structural, number unverified."

| W-NN | plan | claim (one line) | code location Read (file:line) | verdict | classification if drift | evidence (file:line + actual dollar math) | cross-ref |
| ---- | ---- | ---- | ---- | ---- | ---- | ---- | ---- |
| W-085 | carry_fwd_design | Multi-hop: each successive CF re-applies settle-and-roll incl. previous bumps as part of estimate | `carry_forward_service.py:803-807,891-892,941-948`; partition `:258-278` | HOLDS | -- | Target lookup deliberately OMITS the `is_override` filter (`:941-948` filters only template_id/period/scenario/is_deleted; docstring `:803-807`) so a prior-bumped canonical (is_override=True from hop 1) is FOUND, not skipped/duplicated. Bump is additive: `target_row.estimated_amount = target_row.estimated_amount + leftover` (`:891-892`). Partition (`:258-278`) filters `status_id==projected` only (NOT is_override), so a bumped-still-Projected canonical is a valid next-hop source. Numeric: hop1 E=100 -> 100+L1(35)=135; hop2 reads estimated_amount=135 (incl. L1), leftover2=max(0,135-entries2), bumps to 135+L2. Prior bump IS in the estimate. | A-02, A-03, W-098 |
| W-086 | carry_fwd_design | Wife overspent: entries>estimate -> settled actual=entry sum, NO leftover bump | `carry_forward_service.py:879,887`; `transaction_service.py:153` | HOLDS | -- | `leftover = max(Decimal("0"), estimated - entries_sum)` (`:879`). Overspend: estimated=$100, entries_sum=$130 -> `max(0, 100-130)=max(0,-30)=$0`. `if leftover > 0:` (`:887`) False -> target untouched. `settle_from_entries` sets `actual_amount = compute_actual_from_entries = $130` (`transaction_service.py:153`). Settled actual = full overspend; no bump. Exact. | W-097, F-027 |
| W-087 | carry_fwd_design | Wife spent zero: entries sum 0 -> settled actual=0, full estimate carries as leftover | `entry_service.py:446`; `carry_forward_service.py:879,887-894` | HOLDS | -- | `compute_actual_from_entries([])` = `sum((), Decimal("0"))` = `$0` (`entry_service.py:446`). leftover = `max(0, $135 - $0) = $135` (full estimate). `if leftover>0` True -> target bumped by $135 (`:891-892`); source `actual_amount=$0`(`transaction_service.py:153`). Exact. | W-097, W-117 |
| W-088 | carry_fwd_design | Untracked (template-linked discrete) move whole + is_override True (33cd21e) | `carry_forward_service.py:397-421` | HOLDS | -- | Discrete `template_ids` bulk UPDATE sets `pay_period_id=target_period_id`, `is_override=True`, `version_id=version_id+1` (`:413-421`), `synchronize_session="fetch"`. WHERE re-checks `status_id==projected` (F-049 race guard). Matches A-07 discrete-template-linked branch. No money figure (row relocation). | A-07, W-119 |
| W-089 | carry_fwd_design | Ad-hoc rows unchanged (33cd21e) | `carry_forward_service.py:423-438` | HOLDS | -- | Ad-hoc `adhoc_ids` (template_id IS NULL) bulk UPDATE sets ONLY `pay_period_id=target_period_id` + `version_id+1` (`:431-438`); does NOT set is_override (outside partial unique index in every state, comment `:382-384`). Matches A-07 ad-hoc sub-split exactly. No money figure. | A-07, W-119 |
| W-090 | carry_fwd_design | Shadow transfers unchanged (33cd21e) | `carry_forward_service.py:454-468` | HOLDS | -- | Shadow branch: de-dup by `transfer_id` (`:456,467`), delegate `transfer_service.update_transfer(transfer_id, user_id, pay_period_id=target, is_override=True)` (`:461-466`) -- moves parent + both shadow legs atomically (Invariant 5). No direct shadow mutation. Matches A-07 transfer branch. No money figure. | A-07, F-031 |
| W-094 | carry_fwd_design | entries_sum from source.entries | `carry_forward_service.py:878`; `entry_service.py:446` | HOLDS | -- | `entries_sum = compute_actual_from_entries(source_txn.entries)` (`:878`) = `sum((e.amount for e in entries), Decimal("0"))` (`entry_service.py:446`). Reads exactly `source_txn.entries`. | W-114, F-027 |
| W-095 | carry_fwd_design | source settled DONE/RECEIVED, actual=entries_sum | `transaction_service.py:144-153`; `carry_forward_service.py:878,896` | HOLDS | -- | `settle_from_entries`: `if txn.is_income: RECEIVED else DONE` (`:144-147`); `txn.actual_amount = compute_actual_from_entries(txn.entries)` (`:153`). entries_sum (`carry_forward_service.py:878`) and actual (`transaction_service.py:153`) both call the SAME pure fn on the SAME `source_txn.entries` (instance-cached after first lazy-load inside `no_autoflush`; fn never mutates entries, docstring `:874-877`) => numerically identical. actual == entries_sum, guaranteed. | W-112, F-027 |
| W-096 | carry_fwd_design | source paid_at = now | `transaction_service.py:150` | HOLDS | -- | `txn.paid_at = paid_at if paid_at is not None else db.func.now()`. `_settle_source_and_roll_leftover` calls `settle_from_entries(source_txn)` with NO paid_at (`carry_forward_service.py:896`) -> `db.func.now()` (server-evaluated at flush). | W-112 |
| W-097 | carry_fwd_design | leftover = max(0, estimated - entries_sum) | `carry_forward_service.py:879` | HOLDS | -- | `leftover = max(Decimal("0"), source_txn.estimated_amount - entries_sum)` -- literal claim, exact. Decimal-clean (estimated_amount is NUMERIC(12,2), entries_sum Decimal). | W-116, W-086 |
| W-098 | carry_fwd_design | target canonical estimated += leftover | `carry_forward_service.py:887-893` | HOLDS | -- | `if leftover > 0:` (`:887`) then `target_row.estimated_amount = target_row.estimated_amount + leftover` (`:891-892`). Additive, conditional on leftover>0. E.g. target $100 + leftover $35 = $135. Exact. | W-117, W-102 |
| W-099 | carry_fwd_design | target canonical is_override=True | `carry_forward_service.py:894` | HOLDS | -- | `target_row.is_override = True` (`:894`), inside `if leftover > 0` block. Blocks recurrence regen per A-03 (`recurrence_engine.py:128`). | A-03, W-117 |
| W-100 | carry_fwd_design | missing canonical: run recurrence then bump | `carry_forward_service.py:888-894,999-1016` | HOLDS | -- | `_find_or_generate_target_canonical`: no existing row -> `recurrence_engine.generate_for_template(template,[target_period],scenario_id)` (`:999`), pick `new_canonical` where `pay_period_id==target_period.id` (`:1002-1005`), return it; caller then bumps `estimated_amount += leftover` + `is_override=True` (`:891-894`). Generate-then-bump order exact. | W-106, W-123 |
| W-101 | carry_fwd_design | envelope rows create NO sibling rows | `carry_forward_service.py:888-896` | HOLDS | -- | Envelope branch bumps the EXISTING/engine-generated canonical IN PLACE (`:891-894`) and settles the source IN PLACE (`settle_from_entries`, pay_period_id unchanged per A-07). Only row materialised is the engine's own canonical when target was empty (one canonical, not a sibling of source). Zero sibling creation -- matches A-02 (envelope_view's carried-sibling model never built). | A-02, A-07 |
| W-102 | carry_fwd_design | period subtotal increases by exactly the leftover bump | `carry_forward_service.py:891-892`; xref F-002/F-004 | HOLDS | -- | Target period grid subtotal sums Projected `effective_amount` (F-004 D1 `grid.py:263-279`). Bumped canonical stays PROJECTED (bump touches estimated_amount+is_override only, NOT status), no actual -> `effective_amount = estimated_amount`, which rose by exactly `leftover` (`:891-892`). Source row settled DONE/RECEIVED -> excluded from Projected subtotal (W-091/F-002 HOLDS). Target subtotal delta = +leftover, exact. Broader same-page subtotal-vs-balance equality is the separate Q-10 question (F-004), not this delta. | F-002, F-004(Q-10) |
| W-103 | carry_fwd_design | balance projection reduced by additional leftover in target period | `carry_forward_service.py:891-892`; xref F-009/F-004 | HOLDS | -- | Bumped target is a Projected expense; balance calculator subtracts its expense amount. Freshly-bumped canonical has NO entries yet -> `_entry_aware_amount` (F-009) = `estimated_amount` (nothing to subtract) = bumped value. Target balance projection drops by an additional `leftover` (`:891-892`). The `_entry_aware_amount` vs `effective_amount` divergence (F-004/Q-10) manifests only once the target later acquires cleared entries -- a DIFFERENT state than the immediate post-CF state this claim scopes. | F-009, F-004(Q-10) |
| W-104 | carry_fwd_design | net forward cash flow across source+target unchanged | `carry_forward_service.py:878-896` | HOLDS | -- | Conservation proof from the arithmetic: total recognised = `entries_sum` (settled on source actual) + `max(0, estimated - entries_sum)` (rolled to target). entries_sum <= estimated: = `entries_sum + (estimated - entries_sum) = estimated` (conserved exactly; e.g. $65 settled + $35 rolled = $100 original). entries_sum > estimated (overspend): = `entries_sum + 0 = entries_sum` (forward flow correctly reflects the actual overspend, never duplicates/loses money). No money created/destroyed by the mechanism. | W-097, W-102 |
| W-106 | carry_fwd_impl | missing canonical via generate_for_template then bump | `carry_forward_service.py:999-1016,888-894`; `recurrence_engine.py:54-177` | HOLDS | -- | Same path as W-100. `generate_for_template` (`recurrence_engine.py:54`) creates a Projected row `estimated_amount=_get_transaction_amount` (`:145,162`), `is_override=False` (`:163`), flush `:170`; `_find_or_generate_target_canonical` returns it (`:1002-1016`); caller bumps (`:891-894`). | W-100, W-123 |
| W-107 | carry_fwd_impl | already-settled target -> ValidationError + atomic batch fail | `carry_forward_service.py:961-982`; `routes/transactions.py:1171-1178` | HOLDS | -- | `_find_or_generate_target_canonical`: 1 existing row + `target_row.status is None or status.is_immutable` -> `raise ValidationError` (`:970-982`). Route `except ValidationError: db.session.rollback(); return str(exc),400` (`:1171-1178`) -- whole uncommitted batch (discrete bulk UPDATEs + prior envelope settles, all pre-commit) reverted. Atomic. | W-115, W-124 |
| W-108 | carry_fwd_impl | is_envelope=True rejected at Marshmallow level on income templates | `schemas/validation.py:218-257` | HOLDS | -- | `TemplateCreateSchema.validate_envelope_only_on_expense` (`@validates_schema`): `if not data.get("is_envelope"): return`; `if txn_type_id is None: return`; `if ref_cache.transaction_type_is_income(txn_type_id): raise ValidationError("Purchase tracking is only available for expense templates.", field_name="is_envelope")` (`:248-257`). Rejected at schema level exactly as claimed. | W-126 |
| W-109 | carry_fwd_impl | transfer_id NOT NULL stays 33cd21e path (no envelope semantics) | `carry_forward_service.py:273-274,454-468` | HOLDS | -- | Partition: `if txn.transfer_id is not None: shadow_txns.append(txn)` (`:273-274`) -- FIRST branch, so transfer rows never reach the envelope `elif`. Processed only via `transfer_service.update_transfer` (`:461-466`). Matches A-07. | A-07, W-090 |
| W-110 | carry_fwd_impl | partition transfer-status then is_envelope; envelope settle+bump; non-envelope move-whole | `carry_forward_service.py:272-278,397-449` | HOLDS | -- | `_build_carry_forward_context`: `if transfer_id is not None: shadow; elif template is not None and template.is_envelope: envelope; else: discrete` (`:272-278`). Envelope -> `_settle_source_and_roll_leftover` (`:445-449`); discrete -> bulk move-whole (`:397-438`). Exact A-07 three-way partition. | A-07, W-101 |
| W-111 | carry_fwd_impl | shared settle_from_entries extracted, used by mark_done AND envelope CF | `transaction_service.py:38`; `routes/transactions.py:596`; `carry_forward_service.py:896` | HOLDS | -- | Single `settle_from_entries` def (`transaction_service.py:38`). Consumers: `mark_done` envelope-with-entries branch `transaction_service.settle_from_entries(txn)` (`routes/transactions.py:596`, gated `template.is_envelope and txn.entries` `:592-594`); carry-forward `_settle_source_and_roll_leftover` (`carry_forward_service.py:896`). One source of truth. Nuance: mark_done gates on `txn.entries` truthy; CF calls unconditionally (empty entries -> actual $0, full rollover) -- both still the same helper. | F-027(S12), W-112 |
| W-112 | carry_fwd_impl | settle_from_entries sets actual=sum, status DONE/RECEIVED, paid_at=now if absent | `transaction_service.py:144-153` | HOLDS | -- | `if txn.is_income: RECEIVED else DONE` (`:144-147`); `txn.paid_at = paid_at if paid_at is not None else db.func.now()` (`:150`); `txn.actual_amount = compute_actual_from_entries(txn.entries)` (`:153`). All three writes exact. | W-095, W-096 |
| W-113 | carry_fwd_impl | settle_from_entries rejects transfers + requires template.is_envelope | `transaction_service.py:106-138` | HOLDS | -- | Guards (cheap-first, pre-relationship to avoid autoflush): is_deleted `:106`; `transfer_id is not None -> ValidationError` (`:111-115`); `template_id is None -> ValidationError` (`:116-120`); `template is None or not template.is_envelope -> ValidationError` (`:121-125`); immutable status `:130-138`. Exact. | W-111, W-115 |
| W-114 | carry_fwd_impl | entries_sum via compute_actual_from_entries | `carry_forward_service.py:586,878` | HOLDS | -- | `_build_envelope_plan` `compute_actual_from_entries(source_txn.entries)` (`:586`); `_settle_source_and_roll_leftover` same (`:878`). Both the planning and mutating paths use the one helper. | W-094, W-121 |
| W-115 | carry_fwd_impl | settled target (DONE/RECEIVED/SETTLED/PAID) -> ValidationError, batch fails no state change | `carry_forward_service.py:970-982`; `routes/transactions.py:1171-1178` | HOLDS | -- | `if target_row.status is None or target_row.status.is_immutable: raise ValidationError` (`:970-982`). `is_immutable` covers DONE/RECEIVED/SETTLED + also CREDIT/CANCELLED -- code is STRICTER than the claim's 4-status list (refuses on more finalised states), which is more conservative and correct; no wrong number. Route rollback (`:1177`) reverts the uncommitted batch. | W-107, W-124 |
| W-116 | carry_fwd_impl | leftover = max(Decimal("0"), source.estimated_amount - entries_sum) | `carry_forward_service.py:879` | HOLDS | -- | `leftover = max(Decimal("0"), source_txn.estimated_amount - entries_sum)` -- literal claim verbatim incl. the `Decimal("0")` floor. | W-097 |
| W-117 | carry_fwd_impl | leftover>0: target.estimated += leftover AND target.is_override=True | `carry_forward_service.py:887-894` | HOLDS | -- | `if leftover > 0:` -> `target_row.estimated_amount = target_row.estimated_amount + leftover` (`:891-892`) and `target_row.is_override = True` (`:894`). Both writes, conditional on leftover>0, exact. | W-098, W-099 |
| W-121 | carry_fwd_impl | preview computes SAME partition + per-row actions (settle X / roll Y, or BLOCKED) | `carry_forward_service.py:482-560,563-754`; `recurrence_engine.py:180-248` | HOLDS | -- | `preview_carry_forward` and `carry_forward_unpaid` BOTH call the shared `_build_carry_forward_context` (`:532` vs `:335`) -> identical partition. `_build_envelope_plan` recomputes `entries_sum`/`leftover` with the same formula (`:586-589`) and `_resolve_envelope_target_fields` mirrors every `_find_or_generate_target_canonical` ValidationError as a `blocked=True`+block_reason_code (`:602-754`). Empty-period prediction uses `can_generate_in_period` (`recurrence_engine.py:180-248`) -- VERIFIED faithful: identical cross-user/rule-None/ONCE/`_match_periods`/`_get_existing_map`-any-existing predicates; effective_from equivalent for the single `[target_period]` call. Read-only mirror, no SILENT_DRIFT. | A-07, W-122 |
| W-122 | carry_fwd_impl | any BLOCKED -> Confirm disabled + batch refuses atomically on POST | `_carry_forward_preview_modal.html:188-199`; `carry_forward_service.py:150-159,970-982`; `routes/transactions.py:1171-1178` | HOLDS | -- | Modal Confirm button `{% if preview.any_blocked or preview.plans|length == 0 %} disabled aria-disabled="true" {% endif %}`(`:193-196`);`any_blocked = any(p.blocked for p in self.plans)`(`carry_forward_service.py:159`). Even if disabled is bypassed, POST ->`_find_or_generate_target_canonical`raises ValidationError (`:976`) -> route`rollback();return str(exc),400`(`:1177-1178`). Disabled UI + atomic server refusal. | W-121, W-124 |
| W-123 | carry_fwd_impl | generate_for_template empty -> ValidationError + atomic refuse | `carry_forward_service.py:999-1016`; `routes/transactions.py:1171-1178` | HOLDS | -- | `created = generate_for_template(...)`; `new_canonical = next((t for t in created if t.pay_period_id==target_period.id), None)` (`:1002-1005`); `if new_canonical is None: raise ValidationError(...)` (`:1006-1015`). generate_for_template returns `[]` on no-rule/ONCE/no-match/any-existing (`recurrence_engine.py:84,89,94,141`). Route rollback -> atomic. | W-106, W-124 |
| W-124 | carry_fwd_impl | all CF-loop ValidationErrors roll back whole batch; route handles ValidationError + NotFoundError | `routes/transactions.py:1164-1178` | HOLDS | -- | `try: count = carry_forward_unpaid(...); db.session.commit()` `except NotFoundError as exc: return str(exc),404` `except ValidationError as exc: db.session.rollback(); return str(exc),400` (`:1164-1178`). commit reached only if zero exceptions; any envelope-loop ValidationError aborts before commit, rollback discards discrete bulk UPDATEs + prior settles. Atomic; both exception types handled. | W-107, W-122 |
| W-125 | carry_fwd_impl | post-CF: source DONE actual $65; target $135 is_override PROJECTED; no new rows; subtotal +$35; balance -$135 not -$100 | `carry_forward_service.py:878-896`; `transaction_service.py:144-153`; xref F-002/F-004/F-009 | HOLDS | -- | Worked example below. All six post-state assertions verified numerically against the cited code. | W-098, F-002, F-004(Q-10) |
| W-126 | carry_fwd_impl | income+is_envelope POST -> 400 with validation message | `schemas/validation.py:218-257`; `routes/templates.py:163-188` | PARTIALLY_HOLDS | PLAN_DRIFT-STRUCTURAL | Schema rejection HOLDS (W-108: `validate_envelope_only_on_expense` raises). Route `create_template`: `errors = _create_schema.validate(request.form); if errors: flash(_flash_message_for_errors(errors),"danger"); return redirect(...)` (`:168-171`) -- and a SECOND defense `_is_tracking_on_non_expense` flash+redirect (`:186-188`). The validation message IS surfaced (flash), but the form route returns **HTTP 302 redirect (Post/Redirect/Get + flash)**, NOT the claimed **400**. Numeric proof no dollar figure changes: the rejection is TOTAL -- no `TransactionTemplate` created, zero `Transaction` rows generated, no estimated/actual/leftover/subtotal/balance figure produced anywhere; only the HTTP envelope differs (302+flash vs 400). | W-108 |

**Worked example (W-125, the canonical Option-F demonstration -- AFFIRMATIVE numeric proof the
mechanism is correct, not an escalation).** Inputs: source envelope row (grocery template),
`estimated_amount = $100.00`, entries summing to `$65.00` (the wife spent $65 of the $100 budget);
target period already holds the next canonical at `estimated_amount = $100.00`, status PROJECTED.

- `entries_sum = compute_actual_from_entries(source.entries)` = `$65.00`
  (`entry_service.py:446`, `carry_forward_service.py:878`).
- `leftover = max(Decimal("0"), $100.00 - $65.00)` = `$35.00`
  (`carry_forward_service.py:879`).
- `leftover > 0` True (`:887`) -> `_find_or_generate_target_canonical` returns the existing
  mutable PROJECTED canonical (`:961-983`); bump: `estimated_amount = $100.00 + $35.00 =
  $135.00` (`:891-892`); `is_override = True` (`:894`). Status unchanged -> still PROJECTED.
- `settle_from_entries(source)` (`:896`): `actual_amount = $65.00`
  (`transaction_service.py:153`), `status_id = DONE` (expense, `:147`), `paid_at = now()`
  (`:150`), `pay_period_id` UNCHANGED (A-07 envelope branch -- source stays in its period).
- No new rows: source mutated in place, target existing canonical mutated in place; the
  only row-materialising path (`generate_for_template`) is NOT taken because the target
  canonical already existed. Row count in both periods unchanged.
- Target period subtotal (Projected `effective_amount` sum, `grid.py:263-279`): the
  bumped canonical's `effective_amount` rose $100.00 -> $135.00, delta **+$35.00**
  (= leftover exactly). Source row now DONE -> excluded from the source-period Projected
  subtotal (W-091/F-002 HOLDS).
- Target balance projection: the Projected expense canonical (no entries yet ->
  `_entry_aware_amount` == `estimated_amount`) reduces the projected balance by **$135.00**
  instead of the pre-bump **$100.00**.

Every figure matches W-125's claim exactly. The Option-F arithmetic is correct.

**Escalation decision.** The rule escalates only VIOLATED/PARTIALLY_HOLDS that produce a
**wrong financial number**. The carry-forward settle/roll/bump mechanism IS the money arithmetic; it
was verified numerically at every site (entries_sum, leftover floor, additive bump,
conditional-on-leftover>0, settle actual==entries_sum via the same pure fn, overspend clamp,
zero-spend full carry, multi-hop additive re-bump, conservation). **All 34 mechanism verdicts are
HOLDS because the dollar math is provably exactly the plan's intent -- there is no wrong number to
escalate.** The single non-HOLDS, W-126 (PARTIALLY_HOLDS), is an HTTP-status / UX deviation (302
redirect+flash vs the claimed 400): the numeric check is that the rejection is TOTAL and produces
ZERO dollar figures (no template, no transactions), so no worked example / sub-finding is warranted
-- the deviation cannot change any monetary value because it aborts before any monetary value is
computed. Recorded one-row PLAN_DRIFT-STRUCTURAL with that numeric proof, consistent with the
escalation convention. This is NOT the P3-cmp-1 "structural by supersession, number unverified"
framing -- here the numbers WERE verified and they are correct.

**P3-cmp-2 verification (a-f):**

- **(a) 35 rows == triage NEEDS-COMPARISON carry-forward list.** Triage residual for this
  cluster: 35; rows produced: 35 (carry_fwd_design 17 + carry_fwd_impl 18). Every W-NN from
  the `03_consistency.md:4530` cluster definition appears exactly once; the enumerated lists
  reconcile to 35. W-091/092/093/105 (COVERED) and W-118/119/120 (DUP-0.3) correctly
  excluded. **PASS.**
- **(b) Every verdict cites code Read this session.** `carry_forward_service.py` read in
  full (1016 lines); `settle_from_entries` `:38-168`; `compute_actual_from_entries`
  `:428-446`; `generate_for_template` `:54-177` + `can_generate_in_period` `:180-248`;
  `validation.py:170-257`; `transactions.py:1071-1183` + `mark_done:585-619`;
  `templates.py:163-252`; the preview modal in full. No verdict inherited from a plan's
  "planned-per-plan" self-report (every priors row was `planned-per-plan` -- a prior to
  test, tested here by source read). **PASS.**
- **(c) Every wrong-number VIOLATED/PARTIALLY has a worked example.** Zero wrong-number
  non-HOLDS: all 34 mechanism claims HOLD with the dollar math shown inline; W-126
  PARTIALLY_HOLDS produces zero dollar figures (numeric proof recorded, no escalation
  warranted). The W-125 worked example is recorded as the AFFIRMATIVE proof the
  settle/roll/bump arithmetic is correct (the prompt's mandate to show the dollar math for
  settle/roll/bump claims, satisfied even though the verdict is HOLDS). **PASS.**
- **(d) Every PLAN_DRIFT-STRUCTURAL row states the numeric check proving no dollar figure
  changes.** W-126: the rejection is total -- no TransactionTemplate, no Transaction rows,
  no estimated/actual/leftover/subtotal/balance figure produced; only HTTP 302+flash vs 400
  differs. Numeric proof recorded in the row and the escalation decision. **PASS.**
- **(e) Self-spot-check: 6 random verdicts re-Read (mix; >=2 touch settle/roll/bump).**
  1. **W-097 HOLDS** -- re-read `carry_forward_service.py:879`:
     `leftover = max(Decimal("0"), source_txn.estimated_amount - entries_sum)`. Re-did
     math: estimated $100 - entries $65 = $35 = max(0,$35). Verbatim. Confirmed.
  2. **W-095 HOLDS** (settle arithmetic) -- re-read `transaction_service.py:153`
     (`txn.actual_amount = compute_actual_from_entries(txn.entries)`) +
     `carry_forward_service.py:878`. Both call the same pure `sum((e.amount...),Decimal("0"))`
     on the same `source_txn.entries`; actual == entries_sum. Confirmed.
  3. **W-098 HOLDS** (bump arithmetic) -- re-read `carry_forward_service.py:887-892`:
     `if leftover > 0:` then `target_row.estimated_amount = target_row.estimated_amount +
     leftover`. $100 + $35 = $135. Additive, conditional. Confirmed.
  4. **W-089 HOLDS** -- re-read `carry_forward_service.py:423-438`: ad-hoc bulk UPDATE sets
     `pay_period_id` + `version_id+1` only, NO `is_override` (vs template-linked `:413-421`
     which does set is_override). Matches A-07 sub-split. Confirmed.
  5. **W-126 PARTIALLY_HOLDS** -- re-read `templates.py:168-171`:
     `errors = _create_schema.validate(...); if errors: flash(...); return redirect(...)`.
     `redirect()` => HTTP 302, not 400. Schema raises (W-108) but route is 302+flash.
     Confirmed PARTIALLY_HOLDS / PLAN_DRIFT-STRUCTURAL.
  6. **W-085 HOLDS** (multi-hop roll arithmetic) -- re-read `carry_forward_service.py:803-807`
     (docstring: lookup omits is_override "so subsequent carry-forwards re-bump the same
     row") + `:941-948` (filter has no is_override clause) + `:891-892` (additive). Hop2
     reads estimated incl. hop1's L1; re-bumps additively. Confirmed.
  **Spot-check pass rate: 6/6** (3 of which touch settle/roll/bump arithmetic: W-095, W-098, W-085).
  No 2+ failures; stop-condition not triggered.
- **(f) Every UNKNOWN names the blocking Q.** Zero UNKNOWN verdicts in this cluster -- the
  Option-F mechanism is fully resolved by A-02/A-03/A-07 (C-01/C-02/C-06 closed), so no
  claim inherits a pending Q-08..Q-17. (W-102/W-103/W-104/W-125 cross-ref F-004's Q-10 for
  the *broader* same-page subtotal-vs-balance question, but their own narrower
  immediate-post-CF delta claims are numerically provable and verdicted HOLDS, not deferred
  -- the bumped target has no entries so `effective_amount == _entry_aware_amount` at that
  instant.) **PASS (vacuous).**

P3-cmp-2 complete: 35 carry-forward Option-F mechanics verdicts (34 HOLDS, 1 PARTIALLY_HOLDS; 0
VIOLATED, 0 UNKNOWN, 0 VIOLATED-DEAD, 0 N/A). Zero wrong-number escalations -- and, per the mandated
asymmetry, this is because the settle/roll/bump money arithmetic was verified NUMERICALLY and is
provably correct at every site, NOT because the drift was waved through as "structural." The lone
PARTIALLY_HOLDS (W-126) is an HTTP-status deviation that produces no dollar figure. The
carry-forward mechanism is LIVE (route registered `app/__init__.py:499`; grid invokes preview
`grid/grid.html:117`; modal Confirm posts to `transactions.carry_forward`). Phase 3 is **NOT**
complete -- P3-cmp-3 (calendar/analytics/deletion), P3-cmp-4 (loan/strategy/transfer), P3-cmp-5
(ops/audit/misc), then **P3-reconcile** (the only session that may declare Phase 3 complete) remain.
F-001..F-056 / triage table / P3-cmp-1 subsection / `Finding IDs used` header unmodified
(append-only). No source, test, or migration file modified. Not committed; developer reviews between
sessions.

#### P3-cmp-4 verdicts: loan/strategy + transfer-shape

Session P3-cmp-4, 2026-05-16. Fresh-code-read session: for each of the 20 W-NN the code that should
embody the claim was located and Read **in full at source THIS session** --
`amortization_engine.calculate_remaining_months`/`calculate_monthly_payment`/
`_build_rate_change_list`/`_find_applicable_rate`/`generate_schedule` (`:128-619` full),
`debt_strategy_service._sort_debts`/`_apply_minimum_payments`/`_cascade_extra_payments`/
`calculate_strategy` (`:298-639`), `savings_dashboard_service._compute_debt_summary` (`:802-876`
full), `obligations.summary` (`:262-423` full) + `amount_to_monthly`
(`savings_goal_service.py:199-282` full), `transaction.py:128-131`, `transaction_template.py:43-46`,
`transfer_service._validate_positive_amount`/ `create_transfer`/`update_transfer`
(`:60-82,283-613`), `recurrence_engine.py:140-168`, `credit_workflow.mark_as_credit` (`:112-249`),
`transactions.py:400-464`, `carry_forward_service.py:440-479`. Append-only; F-001..F-056, the
`Finding IDs used` header, the triage table, and the P3-cmp-1 / P3-cmp-2 subsections are untouched.
No source/test/migration modified.

**Triage residual reconciliation.** Cluster definition (`03_consistency.md:4532`): P3-cmp-4 =
section5 (11 NC) + transfer_rework_impl (7 NC) + transfer_rework_design (2 NC). The 20 W-NN,
enumerated:

- section5 NC (11): W-219, W-235, W-236, W-237, W-238, W-243, W-244, W-245, W-248, W-251,
  W-252.
- transfer_rework_impl NC (7): W-332, W-333, W-334, W-342, W-343, W-344, W-345.
- transfer_rework_design NC (2): W-305, W-329.

**Triage residual for this cluster: 20; rows produced: 20** (11 + 7 + 2 = 20, reconciled).
W-218/220..234/239..242/246/247/249/250 (COVERED F-013/F-017/F-018/F-022/F-023/F-025/F-026/
F-042..F-050 or DUP-0.3) are NOT in this cluster -- excluded by design, not missed.

**Comparand framing (mandated, two sub-natures).**

- **LOAN/STRATEGY (the 11 section5 entries).** `implementation_plan_section5` is a
  RELEVANT_HISTORICAL plan *document* (priors-0.1: v5 App-B marks the original plan defunct;
  Section 5 was completed from a fresh plan), but the *behavioral claims* it stated for the
  engine edges / debt-strategy / obligations describe behaviour the **completed** Section 5
  work implements (roadmap A.7: payment-linkage to the amortization engine, payoff lifecycle,
  debt summary, savings-goal trajectory -- COMPLETE). Per audit-plan 3.1 the comparand is the
  **current code**; each claim was verified by reading the live arithmetic and computing the
  dollar result by hand (the P3-cmp-2 numeric-asymmetry discipline, NOT the P3-cmp-1
  "structural by supersession" reflex). Resolved gates honoured: A-04 (ARM stored vs fixed
  schedule-walk principal -- dual policy INTENDED; W-248's principal *base* inherits the
  F-008/Q-15 stored-vs-walked question but its *formula* claim is judged on its own and
  HOLDS), A-05 (16-site monthly_payment anchor-reset current -- the W-219/235..238
  edge-mechanic claims are a DIFFERENT axis from F-013's input-triple divergence and are
  verdicted fresh), A-06 (year-end interest pipeline -- not in this cluster's scope).
- **TRANSFER-SHAPE (the 9 transfer_rework_* entries).** Comparand = the CLAUDE.md Transfer
  Invariants (`00_priors.md:111-119` / E-05..E-09): exactly two linked shadows; never
  orphaned; shadow amount/status/period always equal the parent; no path mutates a shadow
  outside the transfer service; balance calculator never queries `budget.transfers`.
  Verification is STRUCTURAL INTEGRITY -- the actual shadow-pair lifecycle was traced in
  code. A genuine invariant violation would be CRITICAL; **none was found** (consistent with
  F-029 AGREE / F-031 zero-violation).

| W-NN | plan | sub-nature | claim (one line) | code location Read (file:line) | verdict | classification if drift | evidence (file:line + dollar math OR shadow-pair trace) | cross-ref |
| ---- | ---- | ---- | ---- | ---- | ---- | ---- | ---- | ---- |
| W-219 | section5 | LOAN/STRATEGY | remaining principal < calculated payment: interest on the smaller balance, excess reduces principal exactly to zero | `amortization_engine.py:517,561-577` (generate_schedule, full fn Read) | HOLDS | -- | No-payment-record branch: `interest = (balance * monthly_rate).quantize(TWO_PLACES,ROUND_HALF_UP)` `:517` -- interest on the CURRENT (smaller) `balance`. `principal_portion = monthly_payment - interest` `:566`; `is_final = (principal_portion >= balance) or (month_num == max_months)` `:572`; if final: `principal_portion = balance` `:574`, `actual_payment = principal_portion + interest` `:575`, `balance = 0` `:577`. Dollar math: balance $500.00, monthly_rate 0.005, monthly_payment $1,000.00 -> interest $2.50 (on $500, the smaller); principal_portion $997.50 >= $500 -> capped to $500.00; payment $502.50; balance $0.00. Exactly the claim. | F-013 (input axis, separate), W-236 |
| W-235 | section5 | LOAN/STRATEGY | rate changes with effective_date before origination must be ignored | `amortization_engine.py:277-281` (`_build_rate_change_list`) | HOLDS | -- | `if origination_date is not None: sorted_changes = [r for r in sorted_changes if r.effective_date >= origination_date]` `:277-281`. Strictly-pre-origination entries dropped; an entry exactly ON origination is kept (correct -- an origination-day rate is not a pre-origination artifact). No off-by-one. | W-234 (COV) |
| W-236 | section5 | LOAN/STRATEGY | payment > remaining principal + interest: principal capped to zero, payment recorded as the exact amount needed | `amortization_engine.py:531,543-547` (payment-record branch) | HOLDS | -- | `principal_portion = total_payment - interest` `:531`; `if principal_portion >= balance:` (i.e. total_payment >= balance + interest) -> `principal_portion = balance` `:544`, `actual_payment = principal_portion + interest` `:545`, `balance = 0` `:547`. Dollar math: balance $1,000.00, interest $5.00, payment-record total $5,000.00 -> principal_portion $4,995.00 >= $1,000 -> capped $1,000.00; recorded actual_payment = $1,005.00 (exact: remaining principal + interest), NOT the $5,000 tendered; balance $0.00. | W-237 |
| W-237 | section5 | LOAN/STRATEGY | schedule terminates (no further rows) once remaining balance reaches zero | `amortization_engine.py:475-476,610-611` | HOLDS | -- | Top-of-loop `if balance <= 0: break` `:475-476`; post-append `if balance <= 0: break` `:610-611`. The zeroing (payoff) row IS appended, then the loop breaks -- no rows after payoff. Terminates exactly at zero. | W-236, W-238 |
| W-238 | section5 | LOAN/STRATEGY | payments in the input list after the loan reaches zero are ignored | `amortization_engine.py:474,520-521,610-611` | HOLDS | -- | `amount_by_month` consulted ONLY inside the loop via `month_key in amount_by_month` `:520-521`; the loop (`for month_num in range(1, max_months+1)`) breaks at payoff `:610-611`, so post-payoff months are never iterated and their payments are never read or applied to the schedule. | W-237 |
| W-243 | section5 | LOAN/STRATEGY | avalanche: prioritise highest interest rate; extra applied to the target debt | `debt_strategy_service.py:323-331,503-512` | HOLDS | -- | Avalanche sort key `(-d.interest_rate, d.current_principal, d.account_id)` `:326-330` -> highest rate first. `_cascade_extra_payments` iterates `sorted_debts`, `if balances[i] <= ZERO: continue`, `extra_pay = min(remaining, balances[i])` `:503-512` -> extra hits the first unpaid (highest-rate) debt. Dollar math: A(rate 0.18,$5,000), B(rate 0.06,$2,000) -> sorted A,B; extra_pool to A until A=0, then cascades. | W-244 |
| W-244 | section5 | LOAN/STRATEGY | snowball: prioritise smallest balance; extra applied to the target debt | `debt_strategy_service.py:333-341,503-512` | HOLDS | -- | Snowball sort key `(d.current_principal, -d.interest_rate, d.account_id)` `:336-340` -> smallest principal first. Same `_cascade_extra_payments` mechanism applies extra to the first unpaid (smallest-balance) debt. Dollar math: A($5,000,0.06), B($2,000,0.18) -> sorted B,A; extra to B first. | W-243 |
| W-245 | section5 | LOAN/STRATEGY | on payoff, the freed payment (minimum + allocated extra) is added to the extra pool for subsequent debts | `debt_strategy_service.py:459-464,514-516,611,619` | HOLDS | (note) parenthetical-gloss | On payoff in `_apply_minimum_payments`: `newly_freed += debt.minimum_payment` (FULL min, future months) `:464` AND `minimum_surplus += debt.minimum_payment - min_pay` (unused capped portion, THIS month) `:462`; in `_cascade_extra_payments`: `newly_freed += debt.minimum_payment` `:516`; `calculate_strategy` step 4 `extra_pool += freed_from_min + freed_from_extra` `:619`, and `available_extra = extra_pool + minimum_surplus` `:611`. The "allocated extra" is never a fixed per-debt sum -- `extra_pool` starts at `extra_monthly` and is NEVER decremented (fully cascaded each month), so adding it again would double-count; the code adds only the freed *minimum* (`:619`) and the extra is structurally always redeployed. Dollar math: A($1,000,0%,min $500), B($10,000,0%,min $200), extra $300, avalanche -> mo1 A=$200; mo2 A min_pay min($500,$200)=$200 -> A=0 payoff: newly_freed +=$500, minimum_surplus += $500-$200=$300; available_extra = $300 pool + $300 surplus = $600 -> all to B; extra_pool -> $300+$500 = $800 for mo3+. Total monthly capacity ($500+$200 mins + $300 extra = $1,000) is conserved and fully redirected to B once A retires. The economic claim holds and the arithmetic is provably exact; the parenthetical "(minimum + allocated extra)" is an explanatory gloss of the conserved capacity, not a literal `pool += min + extra` (which would be a double-count bug). HOLDS, code is more-correct-than-literal (cf. W-030/W-115 convention). | W-243, W-244, F-022/F-023 (payoff-token cross-producer, separate) |
| W-248 | section5 | LOAN/STRATEGY | weighted avg interest rate = sum(rate * principal) / sum(principal) across active loans | `savings_dashboard_service.py:835-843,855-867` (`_compute_debt_summary`, full fn) | HOLDS | -- | Per loan: skip `ad["is_paid_off"]` `:836-837`, skip `principal <= 0` `:842-843`; `total_debt += principal` `:855` (= sum(principal)); `weighted_rate_sum += rate * principal` `:857` (= sum(rate*principal)); `weighted_avg_rate = (weighted_rate_sum / total_debt).quantize(_RATE_PLACES, ROUND_HALF_UP)` `:862-865`. Dollar math: A($300,000,0.065)+B($100,000,0.045) -> (0.065*300000 + 0.045*100000)/(400000) = 24000/400000 = 0.06000 exactly. Formula is verbatim the claim. `principal` reads stored `lp.current_principal` `:840` -- per A-04 ARM-authoritative; the fixed-rate stored-vs-walked base is the F-008/Q-15 question and does NOT alter the formula verdict (claim is the formula shape). | F-008, Q-15 (principal base), F-025 (DTI, separate) |
| W-251 | section5 | LOAN/STRATEGY | monthly equivalents use pattern-to-monthly normalization factors (EVERY_PERIOD = biweekly->monthly, etc.) | `obligations.py:333-338,361,383`; `savings_goal_service.py:261-282` (`amount_to_monthly`, full fn) | HOLDS | -- | `amount_to_monthly`: ONCE -> None `:262`; EVERY_PERIOD -> `amount * 26 / 12` `:265`; EVERY_N -> `amount * 26 / n / 12` `:269`; MONTHLY/MONTHLY_FIRST -> `amount` `:272`; QUARTERLY -> `/3` `:275`; SEMI_ANNUAL -> `/6` `:278`; ANNUAL -> `/12` `:281`. Constants `_PAY_PERIODS_PER_YEAR=Decimal("26")`/`_MONTHS_PER_YEAR=Decimal("12")` (string Decimals, E-10/E-11). `obligations.summary` calls it per active recurring template `:338/361/383`, skipping ONCE `:333-334` and expired `end_date < today` `:335-336`. Dollar math: biweekly $100 -> 100*26/12 = $216.67 (quantize HALF_UP `:348`); ANNUAL $1,200 -> $100.00/mo; QUARTERLY $300 -> $100.00/mo. Factors exact. | W-252, Q-12 (aggregator-owner/SRP, Phase-6, separate) |
| W-252 | section5 | LOAN/STRATEGY | net monthly committed = total monthly income - total monthly outflows (expenses + transfers) | `obligations.py:398-408` | HOLDS | -- | `total_outflows = total_expense_monthly + total_transfer_monthly` `:407`; `net_cash_flow = total_income_monthly - total_outflows` `:408`. Component totals quantized `TWO_PLACES, ROUND_HALF_UP` `:398-406` (A-01-clean). Verbatim income - (expenses + transfers). Dollar math: income $5,000.00 - (expense $3,000.00 + transfer $1,000.00) = $1,000.00. | W-251 |
| W-305 | transfer_rework_design | TRANSFER-SHAPE | every transaction row has a non-nullable account_id FK | `transaction.py:128-131` | HOLDS | -- | `account_id = db.Column(db.Integer, db.ForeignKey("budget.accounts.id", ondelete="RESTRICT"), nullable=False)` -- non-nullable FK; the DB rejects any INSERT with NULL account_id. Schema-level guarantee. | W-332 |
| W-329 | transfer_rework_design | TRANSFER-SHAPE | shadow transactions must not be allowed to be marked as "credit" | `credit_workflow.py:168-169`; `transactions.py:408-428,463` | HOLDS | -- | Shadow-pair trace: a shadow has non-null `transfer_id` (`transfer_service.py:385,406`). The CC-payback entry `mark_as_credit`: `if txn.transfer_id is not None: raise ValidationError("Cannot mark transfer transactions as credit.")` `:168-169`. The transaction-edit route guards `txn.transfer_id` rows EARLIER (`:408-428`, "--- End guard ---" `:428`) and delegates to `transfer_service.update_transfer`, so a shadow NEVER reaches the direct `setattr(txn,'status_id',...)` path `:463`. Invariant 4 intact: no path independently sets a shadow to CC-credit; Invariant 3 (shadow status == parent) is never broken by a unilateral shadow-credit. | F-027 S14 (actual/estimated, separate), F-031 |
| W-332 | transfer_rework_impl | TRANSFER-SHAPE | every transaction has a non-null account_id value at every creation path | `transaction.py:128-131`; `transfer_service.py:383,404`; `recurrence_engine.py:154`; `credit_workflow.py:233` | HOLDS | -- | NOT NULL FK `transaction.py:128-131`. Every creation path supplies it: expense shadow `account_id=from_account_id` `:383`, income shadow `account_id=to_account_id` `:404` (both validated owned accounts); template-gen `account_id=template.account_id` `recurrence_engine.py:154`; credit payback `account_id=txn.account_id` `credit_workflow.py:233`. No NULL-account_id creation path exists. | W-305, W-333, W-334 |
| W-333 | transfer_rework_impl | TRANSFER-SHAPE | template-generated transactions: account_id == template.account_id at creation | `recurrence_engine.py:152-166`; `transaction_template.py:43-46` | HOLDS | -- | `txn = Transaction(account_id=template.account_id, template_id=template.id, ...)` `recurrence_engine.py:153-166` -- account_id copied verbatim from the template. `TransactionTemplate.account_id` is itself `nullable=False` `transaction_template.py:43-46`, so the copied value is always non-null (closes the W-332 NOT NULL chain -- no latent IntegrityError gap). | W-332 |
| W-334 | transfer_rework_impl | TRANSFER-SHAPE | credit payback transactions: account_id == original transaction's account_id | `credit_workflow.py:165,232-243` | HOLDS | -- | `txn = lock_source_transaction_for_payback(transaction_id, user_id)` `:165` (the original); `payback = Transaction(account_id=txn.account_id, ..., credit_payback_for_id=txn.id)` `:232-243` -- payback account_id is exactly the original's account_id. | F-027 S14 (actual/estimated, separate), W-332 |
| W-342 | transfer_rework_impl | TRANSFER-SHAPE | transfers with zero/negative amount rejected at service validation before insert | `transfer_service.py:333,60-82` | HOLDS | -- | `create_transfer` first validation statement `amount = _validate_positive_amount(amount)` `:333` -- BEFORE `db.session.add(xfer)` `:376` / shadow construction `:382-421`. `_validate_positive_amount`: `if amount <= 0: raise ValidationError("Transfer amount must be positive.")` `:78-81`. No shadow pair is ever materialised for an invalid amount (Invariant 2 -- never a half-built pair). | W-343, F-029 |
| W-343 | transfer_rework_impl | TRANSFER-SHAPE | transfers where from_account == to_account rejected at service validation | `transfer_service.py:335-338` | HOLDS | -- | `if from_account_id == to_account_id: raise ValidationError("Source and destination accounts must be different.")` `:335-338` -- before `db.session.add(xfer)` `:376`. No self-transfer shadow pair created. | W-342 |
| W-344 | transfer_rework_impl | TRANSFER-SHAPE | carried-forward transfers: is_override=True on the transfer AND both shadows | `carry_forward_service.py:461-466`; `transfer_service.py:481,596-600` | HOLDS | -- | Shadow-pair trace: CF transfer branch calls `transfer_service.update_transfer(txn.transfer_id, user_id, pay_period_id=target, is_override=True)` `:461-466` (the authorized mutator -- Invariant 4). `update_transfer` fetches the pair via `_get_shadow_transactions(transfer_id)` `:481` (raises if count != 2 -- Invariant 1), then `xfer.is_override = flag; expense_shadow.is_override = flag; income_shadow.is_override = flag` `:596-600` -- all three set True. Invariant 3 (shadows == parent) preserved. | W-345, A-07, F-031 |
| W-345 | transfer_rework_impl | TRANSFER-SHAPE | on carry-forward both shadows move atomically to the target period with the parent | `carry_forward_service.py:455-470`; `transfer_service.py:481,536-541,602` | HOLDS | -- | Shadow-pair trace: CF de-dups by `transfer_id` `:455-467` (comment `:457-460`: the service moves the parent AND both shadows even if only one shadow was in the query -- self-heals sibling period mismatch). `update_transfer`: `_get_shadow_transactions` `:481` (exactly-2 guard); `xfer.pay_period_id = new_period_id; expense_shadow.pay_period_id = new_period_id; income_shadow.pay_period_id = new_period_id` `:539-541`; single `db.session.flush()` `:602` -> all three rows move in one flush (atomic). Invariant 3 (period == parent), Invariant 2 (never orphaned). | W-344, F-031 |

**Escalation decision.** The rule escalates (a) any loan/strategy VIOLATED/PARTIALLY_HOLDS that
changes a dollar figure, and (b) ANY transfer-shape invariant violation (CRITICAL). Walked every
row: **all 20 are HOLDS.** The 11 loan/strategy money-math claims were each verified by computing
the dollar result by hand against the plan's stated behaviour (worked inputs inline in every
evidence cell) -- the arithmetic is provably exactly the plan's intent at every site, so there is
**no wrong number to escalate** (this is the P3-cmp-2 numeric-verification posture, not the P3-cmp-1
"structural, number unverified" reflex). The single (note) row, W-245, is HOLDS with the dollar math
shown (capacity conserved and redirected); its parenthetical-gloss note explains why a literal
`pool += min + extra` would be a double-count *bug* and the code is correctly more-conservative --
no dollar figure is wrong. The 9 transfer-shape claims were verified by tracing the actual
shadow-pair lifecycle; **zero invariant violations** -- every shadow mutation flows through the
authorized transfer service (Invariant 4), the pair is always exactly two and never orphaned
(Invariant 1/2, `_get_shadow_transactions` exactly-2 guard), shadow amount/status/period/is_override
always equal the parent's (Invariant 3), and no balance path queries `budget.transfers` (Invariant
5, cross-ref F-031). No CRITICAL sub-finding warranted; no worked-example escalation required (all
worked examples are recorded inline as AFFIRMATIVE proofs, not escalations).

**P3-cmp-4 verification (a-f):**

- **(a) 20 rows == triage NEEDS-COMPARISON count for this cluster.** Triage residual for
  this cluster: 20; rows produced: 20 (section5 11 + transfer_rework_impl 7 +
  transfer_rework_design 2). Every W-NN from the `03_consistency.md:4532` cluster definition
  appears exactly once; the enumerated lists reconcile to 20. **PASS.**
- **(b) Every verdict cites code Read this session.** `amortization_engine.py:128-619`
  (helpers + full `generate_schedule`); `debt_strategy_service.py:298-639`
  (`_sort_debts`/`_apply_minimum_payments`/`_cascade_extra_payments`/`calculate_strategy`);
  `savings_dashboard_service.py:802-876` (full `_compute_debt_summary`);
  `obligations.py:262-423` (full `summary`) + `savings_goal_service.py:199-282` (full
  `amount_to_monthly`); `transaction.py:128-131`; `transaction_template.py:43-46`;
  `transfer_service.py:60-82,283-440,443-613`; `recurrence_engine.py:140-168`;
  `credit_workflow.py:150-249`; `transactions.py:400-464`;
  `carry_forward_service.py:440-479`. No verdict inherited from a plan's
  `planned-per-plan` self-report (every priors-0.4 row for these W-NN is `planned-per-plan`
  -- a prior to test, tested here by source read). **PASS.**
- **(c) Every loan/strategy wrong-number finding has a worked example with the dollar
  delta.** Zero wrong-number findings (all 11 loan/strategy claims HOLD with the dollar math
  shown inline as affirmative proof; no delta because actual == plan-intended at every
  site). The mandate to show the dollar math for money-movement-adjacent claims is satisfied
  even though every verdict is HOLDS. **PASS (vacuous on escalation; affirmative math
  recorded).**
- **(d) Every transfer-shape violation cites the exact invariant-breaking path.** Zero
  transfer-shape violations: the shadow-pair lifecycle was traced for all 9 entries and
  every mutation flows through the authorized `transfer_service` (Invariant 4); the
  exactly-2 guard (`_get_shadow_transactions`), the before-insert amount/self-transfer
  rejections, and the atomic single-flush period/override propagation were each cited. No
  PLAN_DRIFT-STRUCTURAL row exists in this cluster (no drift at all), so the
  "numeric/structural proof nothing breaks" requirement is vacuously satisfied. **PASS.**
- **(e) Self-spot-check: 6 random verdicts re-Read (>=2 loan/strategy money-math, >=2
  transfer-shape invariant).**
  1. **W-219 HOLDS** (loan/strategy money-math) -- re-read `amortization_engine.py:517,566,
     572-577`: interest computed on current `balance` `:517`; `is_final` caps
     `principal_portion = balance`, `actual_payment = principal_portion + interest`,
     `balance = 0`. Re-did math $500/$2.50 -> capped $500, payment $502.50. Confirmed.
  2. **W-245 HOLDS** (loan/strategy money-math, the trickiest) -- re-read
     `debt_strategy_service.py:459-464` (full min to `newly_freed`, unused-capped to
     `minimum_surplus`), `:611` (`available_extra = extra_pool + minimum_surplus`), `:619`
     (`extra_pool += freed_from_min + freed_from_extra`). Re-walked the A/B example: A's
     $500 min freed to pool, $300 surplus cascaded same month, capacity conserved. The
     parenthetical-gloss reasoning re-confirmed (literal `+extra` would double-count).
     Confirmed.
  3. **W-248 HOLDS** (loan/strategy money-math) -- re-read
     `savings_dashboard_service.py:855,857,863`: `total_debt += principal`,
     `weighted_rate_sum += rate*principal`, `(weighted_rate_sum/total_debt).quantize`.
     Re-did $300k@6.5% + $100k@4.5% = 24000/400000 = 0.06. Confirmed.
  4. **W-329 HOLDS** (transfer-shape invariant) -- re-read `credit_workflow.py:168-169`
     (`if txn.transfer_id is not None: raise ValidationError`) and `transactions.py:408-428`
     (shadow guard delegates to `update_transfer`, returns before the direct setattr path
     `:463`). A shadow cannot be independently CC-credited. Confirmed.
  5. **W-344 HOLDS** (transfer-shape invariant) -- re-read `carry_forward_service.py:461-466`
     (`update_transfer(..., is_override=True)`) and `transfer_service.py:596-600`
     (`xfer/expense_shadow/income_shadow.is_override = flag`). All three set; via the
     authorized mutator. Confirmed.
  6. **W-345 HOLDS** (transfer-shape invariant) -- re-read `transfer_service.py:481`
     (`_get_shadow_transactions` exactly-2), `:539-541` (period set on parent + both
     shadows), `:602` (single flush). Atomic three-row move. Confirmed.
  **Spot-check pass rate: 6/6** (3 loan/strategy money-math: W-219, W-245, W-248; 3 transfer-shape
  invariant: W-329, W-344, W-345). No 2+ failures; stop-condition not triggered.
- **(f) Every UNKNOWN names the blocking Q.** Zero UNKNOWN verdicts in this cluster. The
  section5 edge/strategy/obligations claims are fully resolved by source read (no behaviour
  is ambiguous: each claim's intended behaviour is unmistakable and the code matches);
  W-248's principal-base cross-refs F-008/Q-15 but its *formula* claim is independently
  provable and verdicted HOLDS, not deferred. The transfer-shape claims are resolved by the
  CLAUDE.md invariants + A-07. No new question (Q-18+) is raised -- nothing in this cluster
  has unclear intended behaviour. **PASS (vacuous).**

P3-cmp-4 complete: 20 loan/strategy + transfer-shape verdicts (**20 HOLDS**; 0 PARTIALLY_HOLDS, 0
VIOLATED, 0 UNKNOWN, 0 VIOLATED-DEAD, 0 N/A). Zero escalations -- the 11 loan/strategy money-math
claims are HOLDS
*because the dollar arithmetic was verified by hand and is provably exactly the plan's intent* (not
waved through as structural), and the 9 transfer-shape claims are HOLDS
*because the shadow-pair lifecycle was traced and every invariant holds* (zero CRITICAL). The
section5 engine-edge / debt-strategy / obligations behaviour and the transfer shadow-pair shape are
LIVE and correct. Phase 3 is **NOT** complete -- P3-cmp-3 (calendar/analytics/deletion) and P3-cmp-5
(ops/audit/misc), then **P3-reconcile** (the only session that may declare Phase 3 complete) remain.
F-001..F-056 / triage table / P3-cmp-1 / P3-cmp-2 subsections / `Finding IDs used` header unmodified
(append-only). No source, test, or migration file modified. Not committed; developer reviews between
sessions.

#### P3-cmp-3 verdicts: calendar/analytics/deletion

Session P3-cmp-3, 2026-05-16. Fresh-code-read session: for each of the 40 W-NN the code that should
embody the claim was located and Read **in full at source THIS session** --
`analytics._build_calendar_weeks`/`_render_month_view`/`calendar_tab`
(`analytics.py:107-168, 311-352, 380-428`), the entire `calendar_service.py` (517 lines),
`budget_variance_service.py:1-405` (`compute_variance`/`_get_transactions_for_window`/
`_query_by_period`/`_query_by_date_range`/`_build_txn_variance`/`_compute_actual`/`_pct`),
`dashboard_service._get_upcoming_bills`/`txn_to_bill_dict` (`:99-198`),
`recurrence_engine._compute_due_date` (`:610-669`), `transactions.mark_done`/ `update_transaction`
paid_at lifecycle (`:388-467, 533-622`), the full `archive_helpers.py` (132 lines),
`templates.hard_delete_template` (`:561-637`), `transfers.hard_delete_transfer_template`
(`:596-670`), `accounts.hard_delete_account` (`:596-741`), `categories.delete_category`
(`:178-205`), `grid.py:279-317,337-346` (category dispatch), the full `_calendar_month.html` (130
lines) and `calendar.js` (62 lines). Append-only; F-001..F-056, the `Finding IDs used` header, the
triage table, and the P3-cmp-1 / P3-cmp-2 / P3-cmp-4 subsections are untouched. No
source/test/migration modified.

**Triage residual reconciliation.** Cluster definition (`03_consistency.md:4531`): P3-cmp-3 =
calendar_totals (21) + section8 calendar/variance (9: W-273,274,276..279,281,282,288) + section5a
deletion/archive (10: W-262..271). The 40 W-NN, enumerated:

- calendar_totals (21): W-064, W-065, W-066, W-067, W-068, W-069, W-070, W-071, W-072,
  W-073, W-074, W-075, W-076, W-077, W-078, W-079, W-080, W-081, W-082, W-083, W-084.
- section8 calendar/variance (9): W-273, W-274, W-276, W-277, W-278, W-279, W-281, W-282,
  W-288.
- section5a deletion/archive (10): W-262, W-263, W-264, W-265, W-266, W-267, W-268, W-269,
  W-270, W-271.

**Triage residual for this cluster: 40; rows produced: 40** (21 + 9 + 10 = 40, reconciled).
W-272/W-275/W-280/W-283..287 (section8, COVERED/SUPERSEDED in the triage) and W-253..261 (section5a,
COVERED) are NOT in this cluster -- excluded by the triage, not missed.

**Comparand framing (mandated -- this cluster's financial trap is FILTER-DISCIPLINE DRIFT, not
cosmetic).** The canonical filter sets the calendar/analytics aggregates are measured against, each
Read at source this session and cross-referenced (NOT re-derived) from the P3-a/P3-d findings:

- `Transaction.effective_amount` 4-tier (`transaction.py:238-245`): tier-1 `is_deleted ->
  Decimal("0")`, tier-2 `status.excludes_from_balance -> Decimal("0")` (Credit + Cancelled,
  `ref_seeds.py:79-84`), tier-3 `actual_amount if not None`, tier-4 `estimated_amount`.
- Grid period-subtotal canonical (F-004 D1, `grid.py:263-279`): **Projected-only**
  (`status_id != projected_id: continue` `:269`), `is_deleted` re-checked `:269`,
  income/expense split, raw `txn.effective_amount` `:272,274`. F-004 verdict is **UNKNOWN,
  blocked Q-10** -- `period_subtotal` has NO canonical owner; `02_concepts.md:274-323`
  catalogs **the calendar totals explicitly as period_subtotal "Definition 3 (per-domain
  analytics totals ... each compute a period total with their own status and type
  filters)"**, primary path `UNKNOWN`, governed by Q-10.
- F-009 entries-load matrix: `grid.index`/dashboard `selectinload(Transaction.entries)`;
  `/savings`, `/accounts`, net-worth do NOT -> `_entry_aware_amount` falls back to
  `effective_amount`. The "NO selectinload" side is the symptom-#1 SILENT_DRIFT side.
- F-011 (credit) AGREE, F-012 (Invariant 5) AGREE, F-027 S10 (`budget_variance_service`
  EQUIVALENT: query filters `is_deleted False` + `~status_id.in_([CREDIT,CANCELLED])` ==
  effective_amount tier-1+tier-2), F-027 S11 (`calendar_service.py:483` anchor column is
  Phase-4 OUT-OF-SCOPE), F-028/Q-08 (entry-progress estimated-vs-actual base).

The per-domain analytics total is NOT waved through as "display." Each aggregate's filter set was
proven equal-or-divergent to the grid D1 canonical with file:line on BOTH sides, and every
divergence is escalated below with a worked dollar example. Because `period_subtotal`'s primary path
is itself `UNKNOWN/Q-10` (F-004), a calendar/analytics total whose ONLY divergence from grid D1 is
the Definition-1-vs-Definition-3 status set inherits **UNKNOWN-Q-10** (the divergence FACTS are
recorded regardless, mirroring F-004's treatment of D1-vs-D3).

| W-NN | plan | claim (one line) | code location Read (file:line) | verdict | classification if drift | evidence (filter-set comparison / dollar math -- both sides cited) | cross-ref |
| ---- | ---- | ---- | ---- | ---- | ---- | ---- | ---- |
| W-064 | calendar_totals | inline totals show a line only if its total is non-zero | `_calendar_month.html:50-58` | HOLDS | -- | `{% if day.income_total %}` `:52`, `{% if day.expense_total %}` `:55` -- truthy gate hides `Decimal("0")` lines. Display gate only; **NO Jinja arithmetic** (P1-d 1.3.12 `01_inventory.md:2450` classifies this template arithmetic=NO; totals computed in `analytics._build_calendar_weeks`). Non-financial display. | W-068, W-069, P1-d 1.3.12 |
| W-065 | calendar_totals | per-day income_total/expense_total computed from entries list, added to each day dict in `_build_calendar_weeks()` | `analytics.py:413-426` (`_build_calendar_weeks:380-428` full) | HOLDS (+ escalated filter-set DEFINITION_DRIFT) | DEFINITION_DRIFT (status set) -- canonical-match axis **UNKNOWN-Q-10** | Structural claim met verbatim: `income_total = sum(e.amount for e in entries if e.is_income)` `:413-415`, `expense_total = sum(abs(e.amount) for e in entries if not e.is_income)` `:416-418`, both added to the day dict `:419-426`. `e.amount` = `txn.effective_amount` (`calendar_service.py:255`). **Filter-set vs grid D1 (`grid.py:263-279`):** is_deleted EXCLUDED (`calendar_service.py:228` query `is_deleted.is_(False)` + effective_amount tier-1) == grid D1 (`grid.py:269`) -- MATCH; Credit/Cancelled -> `$0` via effective_amount tier-2 (`:255`) == grid D1 (tier-2 + `!=projected`) -- MATCH (F-011); expense formula raw `effective_amount` (`:255`, NOT `_entry_aware_amount`) == grid D1 (`grid.py:274`) -- MATCH. **DIVERGES on status set:** calendar has NO status filter (`_query_transactions_for_range:225-235` filters account/scenario/is_deleted/date only) so Projected + DONE + RECEIVED + SETTLED ALL contribute; grid D1 is Projected-ONLY (`grid.py:269`). This is exactly period_subtotal Definition-3-vs-Definition-1 (`02_concepts.md:289-296`), primary path UNKNOWN/Q-10. Worked example A below. | **F-004 (Q-10)**, F-011, W-066, 02_concepts period_subtotal |
| W-066 | calendar_totals | `popover_html` key removed from each day dict | `analytics.py:398-405,419-426` (`_build_calendar_weeks` full) | HOLDS | -- | Full-fn Read: the only keys built into either day dict are `number, entries, is_paycheck, is_today, income_total, expense_total` (empty cell `:398-405`; populated `:419-426`). NO `popover_html` key anywhere. Absence confirmed by full-fn read. | W-067, W-080 |
| W-067 | calendar_totals | `_build_popover_html()` deleted | `analytics.py` (`grep -n "_build_popover_html"` this session) | HOLDS | -- | `grep` THIS session: zero matches for `_build_popover_html` anywhere in `analytics.py` (or `app/`). Function deleted as claimed. (note) stale docstring drift: `_render_month_view` docstring `analytics.py:315` still says "pre-computes popover HTML for days with transactions" -- doc-only drift, no code/number effect; recorded, not escalated. | W-066, W-080 |
| W-068 | calendar_totals | inline income green only when non-zero, `calendar-day-income` class | `_calendar_month.html:52-54` | HOLDS | -- | `{% if day.income_total %}<div class="calendar-day-income font-mono">${{ "{:,.0f}".format(day.income_total\|float) }}</div>{% endif %}` -- class `calendar-day-income` (CSS green), non-zero gate. Display only. | W-064, W-070 |
| W-069 | calendar_totals | inline expense red only when non-zero, `calendar-day-expense` class | `_calendar_month.html:55-57` | HOLDS | -- | `{% if day.expense_total %}<div class="calendar-day-expense font-mono">...{% endif %}` -- class `calendar-day-expense` (CSS red), non-zero gate. Display only. | W-064, W-070 |
| W-070 | calendar_totals | inline totals whole-dollar `{:,.0f}`, not cents | `_calendar_month.html:53,56` | HOLDS | (note) E-10 display | `"{:,.0f}".format(day.income_total\|float)` `:53`, `...expense_total\|float` `:56` -- whole-dollar format string, **no arithmetic operator** (P1-d 1.3.12 arithmetic=NO `01_inventory.md:2450`). `\|float` is display-only cast (E-10), app-wide Jinja money-format pattern, not a new finding. The Decimal value is produced in the route helper; the template only formats. | W-073, P1-d 1.3.12 |
| W-071 | calendar_totals | cells with entries get `data-day`=day number + `role="button"` | `_calendar_month.html:48` | HOLDS | -- | `{% if day.entries %}data-day="{{ day.number }}" role="button" tabindex="0"{% endif %}`. Exact. Non-financial UI. | W-075, W-076 |
| W-072 | calendar_totals | `<template data-detail-day>` pre-rendered for each day with entries | `_calendar_month.html:70-108` | HOLDS | -- | `{% for week %}{% for day %}{% if day.entries %}<template data-detail-day="{{ day.number }}">...detail table...{% endif %}{% endfor %}{% endfor %}` `:70-108`. Pre-rendered per day-with-entries. Non-financial UI. | W-076, W-073 |
| W-073 | calendar_totals | detail table full amounts `{:,.2f}` with cents | `_calendar_month.html:94-96` | HOLDS | (note) E-10 display | `<td class="...">${{ "{:,.2f}".format(entry.amount\|float) }}</td>` `:95`. `entry.amount` = `effective_amount` (`calendar_service.py:255`). Format string only, no arithmetic (P1-d 1.3.12). `\|float` display cast (E-10, app-wide). | W-070, W-074 |
| W-074 | calendar_totals | status badge "Paid" when `entry.is_paid` else "Projected" | `_calendar_month.html:97-99`; `calendar_service.py:261` | HOLDS | -- | `<span class="badge {{ 'bg-success' if entry.is_paid else 'bg-secondary' }}">{{ "Paid" if entry.is_paid else "Projected" }}</span>` `:97-99`. `entry.is_paid = bool(txn.status and txn.status.is_settled)` (`calendar_service.py:261`) -- ID/flag-based (`is_settled`), NOT a string-name compare. Display only. | W-072 |
| W-075 | calendar_totals | JS binds click handlers to `data-day` cells on `htmx:afterSettle` | `calendar.js:9,22-23` | HOLDS | -- | `document.addEventListener('htmx:afterSettle', ...)` `:9`; `tabContent.querySelectorAll('.calendar-day[data-day]').forEach(function(cell){ cell.addEventListener('click', ...) })` `:22-23`. JS UI only, NO money math (E-17; P1-d 1.4 `01_inventory.md:2686` arithmetic=NO). | W-071, W-076 |
| W-076 | calendar_totals | click clones `<template data-detail-day>` into `#calendar-day-detail` | `calendar.js:17,25-27,45-46` | HOLDS | -- | `detailContainer = ...#calendar-day-detail` `:17`; `template = ...querySelector('template[data-detail-day="'+day+'"]')` `:25-27`; `detailContainer.innerHTML=''; detailContainer.appendChild(template.content.cloneNode(true))` `:45-46`. JS UI only. | W-072, W-075 |
| W-077 | calendar_totals | same-day click toggles detail off + removes `calendar-day--selected` | `calendar.js:31-36` | HOLDS | -- | `if (activeDay === day) { detailContainer.innerHTML=''; cell.classList.remove('calendar-day--selected'); activeDay=null; return; }`. Exact. JS UI. | W-078, W-079 |
| W-078 | calendar_totals | different-day click deselects previous + shows new detail | `calendar.js:39-48` | HOLDS | -- | `if (activeDay !== null) { prev = ...querySelector('.calendar-day--selected'); if (prev) prev.classList.remove('calendar-day--selected'); }` `:39-42`; then show new + `cell.classList.add('calendar-day--selected'); activeDay=day` `:45-48`. Exact. JS UI. | W-077 |
| W-079 | calendar_totals | close button dismisses detail + removes selected highlight | `calendar.js:51-58` | HOLDS | -- | `closeBtn = detailContainer.querySelector('#calendar-detail-close'); if (closeBtn) closeBtn.addEventListener('click', function(){ detailContainer.innerHTML=''; cell.classList.remove('calendar-day--selected'); activeDay=null; })` `:51-58`. Exact. JS UI. | W-077, W-078 |
| W-080 | calendar_totals | all popover code (`disposePopovers`, `bootstrap.Popover`) removed | `calendar.js` (full 62-line Read) | HOLDS | -- | Full-file Read (62 lines): zero `disposePopovers`, zero `bootstrap.Popover`, zero `popover`. Removal confirmed by exhaustive read. | W-066, W-067, W-081 |
| W-081 | calendar_totals | no `data-bs-toggle="popover"` in rendered HTML | `_calendar_month.html` (full 130-line Read) | HOLDS | -- | Full-file Read (130 lines): zero `data-bs-toggle`, zero `popover` anywhere. Confirmed. | W-080 |
| W-082 | calendar_totals | existing tests pass after HTML changes | `tests/test_routes/test_analytics.py` | N/A-TEST-CLAIM | -- | Test-suite pass/fail is not a Phase-3 plan-vs-code money-behavior claim and cannot be verdicted in a read-only audit (hard rules 1, 2: no test runs, no test changes). Test-coverage state is Phase 7's domain (`07_test_gaps.md`). Non-financial. | Phase 7 |
| W-083 | calendar_totals | new tests verify inline-total classes rendered | `tests/test_routes/test_analytics.py` | N/A-TEST-CLAIM | -- | Test existence/quality is Phase 7's domain, not a Phase-3 consistency money claim. Read-only audit does not assert test presence as a watchlist verdict. Non-financial. | Phase 7 |
| W-084 | calendar_totals | new tests verify `<template>` contains txn names | `tests/test_routes/test_analytics.py` | N/A-TEST-CLAIM | -- | Same as W-082/083: Phase-7 test-coverage scope, not a Phase-3 plan-vs-code financial verdict. Non-financial. | Phase 7 |
| W-273 | section8 | `due_date` populated from `day_of_month`, clamped to month last day | `recurrence_engine.py:610-669`; gen `:150,165` | HOLDS | -- | `_compute_due_date`: `dom = rule.day_of_month` `:636`; if `dom is None` (every-paycheck/every-N) return `period.start_date` `:640-641` (documented exception, always populated); else clamp `last_day = cal.monthrange(...)` `:650/660`, `min(dom, last_day)` `:651,661`. Generation calls it unconditionally: `due = _compute_due_date(rule, period)` `:150`, `due_date=due` `:165`. Clamp ("day 31 in Apr -> 30") verbatim. Recurrence mechanic feeding calendar/variance attribution (W-276/W-281). | W-276, W-281 |
| W-274 | section8 | `paid_at` set on Done/Received, cleared on revert to non-settled | `transactions.py:534-537,546,596,610-611,399-400,459-467` | HOLDS | -- | mark_done: income -> RECEIVED `:535`, expense -> DONE `:537`; direct branch `txn.status_id=status_id; txn.paid_at=db.func.now()` `:610-611`; envelope branch `settle_from_entries(txn)` `:596` (writes status+paid_at+actual, F-027 S12 / P3-cmp-2 W-112); transfer-shadow branch `paid_at: db.func.now()` `:546` via service. update_transaction revert: regular `if new_status and not new_status.is_settled and txn.paid_at is not None: revert_paid_at=True` `:459-460` -> `txn.paid_at=None` `:466-467`; shadow `if new_status and not new_status.is_settled: svc_kwargs["paid_at"]=None` `:399-400`. Timestamp lifecycle; does NOT alter calendar money (calendar attributes by `due_date`, `is_paid` reads `status.is_settled` not `paid_at`). | W-273, F-027 S12 |
| W-276 | section8 | monthly attribution: `due_date` else `pay_period.start_date` | `calendar_service.py:225-235,373-397` | HOLDS | -- | `_query_transactions_for_range`: `or_(Transaction.due_date.between(first_day,last_day), Transaction.due_date.is_(None) & Transaction.pay_period_id.in_(period_ids))` `:229-234`. `_get_display_day`: `if txn.due_date is not None:` use `due_date` month/day `:388-391`; else `start = txn.pay_period.start_date` `:394-396`. Attribution exactly "due_date primary, pay_period.start_date fallback." Filter set: `is_deleted.is_(False)` `:228` (== effective_amount tier-1 / grid D1); Credit/Cancelled -> `$0` via `effective_amount` tier-2 at `_build_day_entry:255`. Status-set divergence vs grid D1 is the W-065 Definition-3 issue (cross-ref, not re-escalated here). | W-065, W-273, F-004(Q-10) |
| W-277 | section8 | month-end balance = projected end of last pay period ending in/after the month | `calendar_service.py:435-489` (`_compute_month_end_balance` full) | **UNKNOWN-Q-18** (period-selection axis) + escalated entries-load SILENT_DRIFT | SILENT_DRIFT (entries-load, inherits F-003/F-009); SCOPE_DRIFT (anchor-None, Q-16) | Engine call is correct: `balance_calculator.calculate_balances(account.current_anchor_balance, account.current_anchor_period_id, all_periods, all_txns)` `:482-487`, returns `balances.get(target_period.id, Decimal("0"))` `:489` -- same engine/anchor as grid F-003 Path A. **Three divergences:** (1) period selection `for p in all_periods: if p.end_date <= last_day: target_period = p` `:461-466` = LAST period ending ON/BEFORE month-end; the plan claim says "ending in or **after** the month." A boundary-straddling period is excluded and the PRIOR period's balance is shown -- ambiguous intended behavior -> **Q-18** (this file). (2) `all_txns` query `:471-480` has **NO `selectinload(Transaction.entries)`** -> per F-009 matrix this is the symptom-#1 "NO" side: `_entry_aware_amount` falls back to `effective_amount`, so calendar month-end balance DIVERGES from grid (entries-loaded) for a Projected envelope expense with cleared entries -- inherits **F-003/F-009 SILENT_DRIFT** (worked example B). (3) `if account.current_anchor_period_id is None: return Decimal("0")` `:449-450` -- the Q-16 anchor-None axis ("zero" representation, like net-worth/dashboard). | **F-003, F-009, Q-16, Q-18** |
| W-278 | section8 | large flag if `effective_amount` exceeds user `large_transaction_threshold` | `calendar_service.py:255,262`; `analytics.py:143` | HOLDS | (note) fn-cite + boundary | Substance holds: `is_large = abs(amount) >= threshold` where `amount = txn.effective_amount` (`calendar_service.py:255,262`) and `threshold = settings.large_transaction_threshold if settings else 500` (`analytics.py:143`, the user's configured value). Two (note)s, no money error: (a) claim cites `_get_display_day()` but the flag is in `_build_day_entry:262` (function mis-attribution in the plan; behavior present). (b) claim says "exceeds" (`>`), code is `>=` (`:262`) -- a txn exactly at the threshold is flagged large; this is a display highlight, not a money figure, and `>=` ("at or above") is a defensible reading of "large" (same more-correct/boundary precedent as W-030/W-115). | W-279 |
| W-279 | section8 | 3rd-paycheck-month detection by counting period start_dates; biweekly = 2/yr | `calendar_service.py:419-432` (`_detect_third_paycheck_months`) | HOLDS | -- | `month_counts: dict[int,int] = defaultdict(int); for p in periods: if p.start_date.year == year: month_counts[p.start_date.month] += 1; return {m for m,count in month_counts.items() if count >= 3}` `:427-432`. Counts pay-period `start_date`s per calendar month, flags months with `>= 3`. The "biweekly produces exactly 2 such months/year" is an arithmetic property of biweekly cadence, NOT code-enforced -- the code correctly detects `>=3` generically; HOLDS as the detection mechanic. No money figure. | W-277 |
| W-281 | section8 | txn attributed to `due_date` month else `pay_period` start | `budget_variance_service.py:284-296,231-258,177-228` | HOLDS | -- | `_query_by_date_range`: `or_(due_date.between(first_day,last_day), due_date.is_(None) & pay_period_id.in_(overlapping period_ids))` `:289-294` -- identical attribution to calendar (W-276). Pay-period window uses `_query_by_period` `:231-258`. **Filter set MATCHES the canonical (F-027 S10 EQUIVALENT):** `Transaction.is_deleted.is_(False)` `:287` (== effective_amount tier-1) AND `~Transaction.status_id.in_(excluded_status_ids)` `:288` where `excluded_status_ids = [CREDIT, CANCELLED]` `:207-210` (== effective_amount tier-2 `ref_seeds.py:79-84`). Both sides cited; the omitted effective_amount tiers provably cannot reach the hand-rolled `_compute_actual` (F-027 S10). | **F-027 S10**, W-276, W-282 |
| W-282 | section8 | variance pct = (variance / estimated) * 100 when estimated != 0, else null | `budget_variance_service.py:396-404` (`_pct`) | HOLDS | -- | `_pct`: `if estimated == Decimal("0"): return None` `:402-403`; `return (variance / estimated * _HUNDRED).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)` `:404`, `_HUNDRED=Decimal("100")` / `_TWO_PLACES=Decimal("0.01")` `:34-35`, `ROUND_HALF_UP` `:16` (A-01-clean: string Decimals + HALF_UP). Dollar math: variance `$30.00`, estimated `$120.00` -> `30/120*100 = 25.00%`; estimated `$0.00` -> `None`. Verbatim the claim. `variance`/`estimated` base = F-027 S10 EQUIVALENT (query-guarded tiers). | W-281, F-027 S10 |
| W-288 | section8 | upcoming bills = unpaid expense, current period remainder + full next, sorted due_date asc | `dashboard_service.py:99-164,191` (`_get_upcoming_bills`/`txn_to_bill_dict`) | HOLDS | (note) "remainder" wording | `period_ids = [current_period.id]` + `next_period.id` if present `:122-124`. Filter: `account_id`, `scenario_id`, `pay_period_id.in_(period_ids)` `:143`, `is_deleted.is_(False)` `:144`, `status_id == projected_id` `:145` (unpaid), `transaction_type_id == expense_type_id` `:146`. Sort: `bills.sort(key=lambda b: (b["_sort_date"], b["name"]))` `:159` where `_sort_date = txn.due_date if txn.due_date else txn.pay_period.start_date` `:154` (due_date asc, then name; period-start fallback). **Filter discipline MATCHES grid D1 canonical:** Projected-only (`:145` == `grid.py:269`), is_deleted excluded (`:144`), per-bill `amount = txn.effective_amount` (`:191`, canonical). The plan's "remainder of the current period" is achieved naturally -- the Projected-only filter excludes already-paid (DONE/RECEIVED) bills, so what remains IS the unpaid remainder; an unpaid-but-overdue bill is correctly still listed (you still owe it). A bill LIST, not a money aggregate -> no total-filter-divergence; per-bill amount is canonical. Entry-progress fields feed the Q-08/F-028 cluster (cross-ref, not re-verdicted). | F-004 D1, F-028(Q-08) |
| W-262 | section5a | txn templates with Paid/Settled history cannot hard-delete; archive instead | `templates.py:561-637`; `archive_helpers.py:17-38` | HOLDS (+ escalated rule-4 data-loss) -- RECEIVED axis **UNKNOWN-Q-19** | (note) latent silent data-loss on RECEIVED-only income templates | Code matches the literal "Paid or Settled" claim: `if archive_helpers.template_has_paid_history(template.id):` -> `flash(...archived instead)`, `if template.is_active: template.is_active=False`, soft-delete Projected (query filters `is_deleted.is_(False)` `:594`), `return redirect` `:581-609`. `template_has_paid_history`: `status_id.in_([DONE, SETTLED])` AND `is_deleted.is_(False)` `:29-37` (DONE = Paid; is_deleted correctly filtered -- a soft-deleted Paid txn does not block, correct). **Escalated (CLAUDE.md rule 4):** RECEIVED is NOT in `[DONE, SETTLED]` yet RECEIVED is `is_settled=True` (`ref_seeds.py:79-84`) and is what `mark_done` assigns income (`transactions.py:535`). A template whose only history is RECEIVED income txns -> `template_has_paid_history=False` -> `db.session.query(Transaction).filter(template_id==template.id).delete()` `:616-618` permanently destroys RECEIVED income history. Plan-vs-code: code = plan's literal wording (HOLDS); whether the plan's "Paid or Settled" is too narrow -> **Q-19** (this file). | **Q-19**, W-263, W-264 |
| W-263 | section5a | template hard delete removes template + all linked Projected txns | `templates.py:611-637` | HOLDS | -- | No-history branch: `db.session.query(Transaction).filter(Transaction.template_id == template.id).delete(synchronize_session="fetch")` `:616-618` (all linked -- comment `:612-614`: only Projected remain after the history check, deleted unconditionally for safety), then `db.session.delete(template)` `:620`, `commit` `:622`. This is an intentional permanent `.delete()` (not a display aggregate) -> the is_deleted-omission/double-count concern does NOT apply (no money is summed). Removes template + all linked (Projected) txns as claimed. | W-262 |
| W-264 | section5a | transfer templates with Paid/Settled history cannot hard-delete; archive instead | `transfers.py:596-659`; `archive_helpers.py:41-62` | HOLDS | -- | `if archive_helpers.transfer_template_has_paid_history(template.id):` -> archived (is_active=False), Projected transfers soft-deleted via `transfer_service.delete_transfer(..., soft=True)` `:644-645` (Invariant 4), `return redirect` `:624-659`. `transfer_template_has_paid_history`: `status_id.in_([DONE, SETTLED])` AND `is_deleted.is_(False)` `:53-60`. **No RECEIVED gap (unlike W-262):** transfers only ever use DONE/SETTLED -- the transfer service sets DONE on both shadows (`transactions.py:541-545`), there is no RECEIVED transfer state -> `[DONE, SETTLED]` is complete here. | W-262, W-265 |
| W-265 | section5a | transfer template hard delete preserves shadow invariants (no orphaned shadows) | `transfers.py:596-670` | HOLDS | -- | Docstring `:599-610` maps all 5 invariants. Archive branch: Projected transfers removed via `transfer_service.delete_transfer(xfer.id, current_user.id, soft=True)` `:644-645` (query first filters `is_deleted.is_(False)` `:640`). No-history branch: ALL linked transfers deleted through `transfer_service.delete_transfer(..., soft=False)` `:661-670` -> CASCADE on `Transaction.transfer_id` removes BOTH shadows atomically (Invariant 1/2/4). No direct shadow mutation; the authorized transfer service is the sole mutator. Cross-ref F-031 (Invariant 5 AGREE), F-029, P3-cmp-4 W-345 (shadow-pair lifecycle). | W-264, F-031, F-029 |
| W-266 | section5a | account delete blocked if any txn/transfer history, regardless of status | `accounts.py:626-684`; `archive_helpers.py:65-87` | HOLDS | -- | Guard 2: ANY `TransferTemplate` (from/to this account) -> block `:628-645`. Guard 3: ANY `TransactionTemplate` -> block `:649-660`. Guard 4: `archive_helpers.account_has_history` = ANY `Transaction` with `is_deleted.is_(False)` (NO status filter, `:82-86`) -> archive `:662-684`. Transfer history is detected via shadow `Transaction` rows (Invariant 1: every transfer has 2 shadow txns with `account_id`) -> "any transaction or transfer history" is covered. Regardless of status confirmed (no status predicate in `account_has_history`). | W-267, W-268, F-031 |
| W-267 | section5a | account hard delete deletes LoanParams/InterestParams/InvestmentParams/AccountAnchorHistory | `accounts.py:711-725` | HOLDS | -- | Step 3 explicit deletes: `db.session.query(LoanParams).filter_by(account_id=account_id).delete()` `:711`, `InterestParams` `:712`, `InvestmentParams` `:713` (+ `EscrowComponent`/`RateHistory`/`SavingsGoal` `:714-716` -- superset). Step 4 `db.session.delete(account)` `:725`; `AccountAnchorHistory` via ORM `cascade="all, delete-orphan"` (docstring `:718-719`). All four named records removed; code deletes a strict superset (correct). | W-266, W-268 |
| W-268 | section5a | account hard delete blocked if active transfer/transaction templates reference it | `accounts.py:628-660` | HOLDS | -- | Guard 2 blocks on ANY `TransferTemplate` with `from_account_id==account_id OR to_account_id==account_id` `:628-638` (active OR archived). Guard 3 blocks on ANY `TransactionTemplate` `filter_by(account_id=account_id)` `:649-653`. Code is STRICTER than the claim's "active" (blocks on archived too) -- correct, because the FK is `ON DELETE RESTRICT`; more-conservative-than-claim precedent (W-115/W-030/W-268). No money figure. | W-266, W-267 |
| W-269 | section5a | category hard delete fails if in use by templates/transactions | `categories.py:178-205`; `archive_helpers.py:90-131` | HOLDS | (note) conservative is_deleted | `if archive_helpers.category_has_usage(category_id, current_user.id):` -> `flash(...archived instead)`, `if category.is_active: category.is_active=False; commit`, `return redirect` `:190-199`; else `db.session.delete(category)` `:202`. `category_has_usage`: ANY `TransactionTemplate(category_id, user_id)` `:114-118` OR ANY `Transaction` joined `PayPeriod` on `user_id` with `category_id` `:124-131` (user-scoped, no IDOR). "Cannot permanently delete an in-use category" met via archive-instead-of-delete (same unified pattern as W-262/264/266). (note) the Transaction-usage check does NOT filter `is_deleted` (`:124-131`) -- intentionally conservative: a category used only by soft-deleted txns is still treated as "in use" and blocks delete. This is an `.exists()` existence check, NOT a money aggregate -> the is_deleted-double-count concern does not apply (no money summed); over-blocking is safe. | W-262, W-264, W-266 |
| W-270 | section5a | archived categories excluded from Add Transaction modal dropdown | `grid.py:283-290,337-346` | HOLDS | -- | `all_categories = query(Category).filter_by(user_id=user_id)...all()` `:283-288` (incl. archived); `active_categories = [c for c in all_categories if c.is_active]` `:290` (`is_active` boolean flag -- ID/flag-based, not string compare). `render_template(..., categories=active_categories, ...)` `:337,346` -- the Add Transaction modal dropdown receives **active-only**; archived excluded. UI dropdown, no money figure. | W-271 |
| W-271 | section5a | txns with archived categories still render in the grid | `grid.py:281-288,312-317`; `grid.py:107,130-131` | HOLDS | -- | `all_categories` (incl. archived, NO `is_active` filter `:283-288`) is passed to `_build_row_keys(row_source_txns, all_categories, ...)` `:312-317`. `_build_row_keys` indexes `cat_by_id = {c.id: c for c in categories}` `:107` and falls back to "Uncategorized" only if the cat id is absent `:130-131`; an archived category IS in `all_categories`, so a txn with an archived `category_id` resolves its group/name and renders. Comment `:281-282` states this intent verbatim. | W-270 |

**Worked example A (W-065 -- the calendar-total filter-set DEFINITION_DRIFT vs grid D1).** This is
the cluster's filter-discipline trap, escalated per the prompt (NOT defaulted to "display"). Inputs,
one calendar month = pay period P (Projected expense `$300.00`, `due_date` in the month) + pay
period P-1 settled history landing in the same month by `due_date` (one DONE expense `$120.00`, one
RECEIVED income `$2,000.00`, both `due_date` in the month; both already reflected in the checking
anchor):

- **Grid D1 period subtotal for P** (`grid.py:263-279`, Projected-only, `effective_amount`):
  income `$0.00`, expense `$300.00`, net `-$300.00` (the DONE/RECEIVED rows are excluded by
  `status_id != projected_id` `:269`).
- **Calendar month total** (`calendar_service.py:_assign_transactions_to_days:301-304`,
  NO status filter, `effective_amount`): `total_income = $2,000.00` (the RECEIVED row,
  `effective_amount = actual`), `total_expenses = $300.00 + $120.00 = $420.00` (Projected
  `$300` + DONE `$120`), `net = $1,580.00` (`_build_month_summary:364`).
- **Delta:** income `+$2,000.00`, expense `+$120.00`, net `+$1,880.00` between the two
  surfaces for the same underlying transactions. The divergence driver is the **status
  set**: calendar has no `status_id` filter (`_query_transactions_for_range:225-235`); grid
  D1 is Projected-only (`grid.py:269`). is_deleted and Credit/Cancelled handling MATCH on
  both sides (calendar `is_deleted` query `:228` + `effective_amount` tier-1/tier-2 `:255`;
  grid `:269` + tiers). This is precisely `period_subtotal` Definition-3-vs-Definition-1
  (`02_concepts.md:289-296`). **Verdict gate:** the canonical-match axis is **UNKNOWN-Q-10**
  -- F-004 establishes `period_subtotal` has no canonical owner; whether the all-status
  calendar total "should" equal the Projected-only grid subtotal is exactly what Q-10 asks.
  The divergence FACTS are recorded here regardless (mirroring F-004's recording of D1-vs-D3
  facts independent of Q-10). W-065's structural plan claim is independently HOLDS.

**Worked example B (W-277 -- entries-load SILENT_DRIFT, the symptom-#1 mechanism on the calendar
month-end balance).** The calendar's `_compute_month_end_balance` queries `all_txns` WITHOUT
`selectinload(Transaction.entries)` (`calendar_service.py:471-480`), so it is on the F-009 matrix
"NO" side. Inputs: checking anchor `A`; the target period holds one Projected envelope expense
`estimated_amount = $500.00` with cleared debit entries summing `$462.34` (entries already reflected
in the checking anchor):

- **Grid current-period balance** (`grid.py:229` `selectinload(entries)` ->
  `_entry_aware_amount` = `max(500.00 - 462.34 - 0, 0) = $37.66`): balance =
  `A - other - $37.66`.
- **Calendar month-end balance** (`calendar_service.py:471-480` NO entries load ->
  `_entry_aware_amount` short-circuits `'entries' not in __dict__` `balance_calculator.py:
  353-354` -> returns `effective_amount = estimated_amount = $500.00`): balance =
  `A - other - $500.00`.
- **Delta:** the calendar month-end balance is **`$462.34` lower** than the grid balance for
  the same account/period/anchor -- identical in mechanism, sign, and controlling dimension
  to symptom #1 / F-009 (the only input difference is the `selectinload`). Classified
  **SILENT_DRIFT**, inherited from F-003/F-009 (not a new finding -- recorded here because
  the calendar is a NEW consuming path on the F-009 "NO" side). The W-277 period-selection
  axis is separately **UNKNOWN-Q-18**; the anchor-None `return Decimal("0")` `:449-450` is
  the Q-16 SCOPE_DRIFT axis.

**Escalation decision.** The cluster rule escalates (i) any calendar/analytics aggregate whose
filter set diverges from the canonical producers, with a worked dollar example; (ii) any
`is_deleted`-omission that double-counts soft-deleted money; (iii) any Jinja financial arithmetic.
Walked every row:

- (i) **Filter-set divergence: exactly one** -- W-065 (and its inherited consumers W-276,
  the calendar-total face of W-277). The calendar month/day totals' status set (all-status)
  diverges from grid D1 (Projected-only); is_deleted and Credit/Cancelled MATCH. Worked
  example A above (delta income `+$2,000`, expense `+$120`, net `+$1,880`). This is
  `period_subtotal` Definition-3-vs-Definition-1 -> canonical-match axis **UNKNOWN-Q-10**
  (F-004), divergence facts recorded. W-277 additionally carries the F-003/F-009 entries-load
  **SILENT_DRIFT** (worked example B) and the Q-18 period-selection ambiguity.
  `budget_variance_service` (W-281/W-282) and `_get_upcoming_bills` (W-288) filter sets were
  proven to **MATCH** the canonical (F-027 S10: `is_deleted False` + `~[CREDIT,CANCELLED]`;
  W-288: Projected-only + is_deleted + `effective_amount`) -- no divergence, no escalation.
- (ii) **is_deleted-omission double-count: none.** Every money-bearing aggregate filters
  is_deleted at the query (`calendar_service.py:228`, `budget_variance_service.py:287`,
  `dashboard_service.py:144`) or via `effective_amount` tier-1. The deletion routes'
  `.delete()` calls (W-263 `templates.py:616-618`, W-265 `transfers.py:661-670`,
  W-267 `accounts.py:711-716`) are intentional permanent removals, NOT display aggregates --
  no money is summed, so the double-count class does not apply. `category_has_usage`'s
  non-is_deleted Transaction check (W-269) is an `.exists()` existence test, conservative
  over-blocking, not a money sum.
- (iii) **Jinja financial arithmetic: none.** `_calendar_month.html` (full Read) contains
  zero arithmetic operators on money -- all totals are produced in `analytics.
  _build_calendar_weeks` (route, Python Decimal) and `calendar_service` and merely
  format-stringed (`{:,.0f}`/`{:,.2f}` + `\|float` display cast). This is independently
  confirmed by P1-d's template-arithmetic inventory: `01_inventory.md:2450` (section 1.3.12)
  classifies `_calendar_month.html` **arithmetic = NO**, and `01_inventory.md:2686`
  (section 1.4) classifies `calendar.js` **arithmetic = NO** (E-17 compliant). The `\|float`
  display cast (E-10) is the app-wide Jinja money-format pattern, not a new finding.
- **Latent silent data-loss (CLAUDE.md rule 4, out of the three escalation classes but
  reported per rule 4): one** -- W-262: `template_has_paid_history` omits RECEIVED, so a
  RECEIVED-only income template is silently hard-deletable, permanently destroying settled
  income history (`archive_helpers.py:29-37` vs `transactions.py:535` vs `ref_seeds.py:79-84`
  `Received.is_settled=True`). No wrong number on any page; an irreversible history loss on
  an explicit user delete. Raised as **Q-19**; W-262's plan-vs-code verdict is HOLDS (code
  matches the plan's literal "Paid or Settled"); Q-19 asks whether the plan definition is
  too narrow.

No CRITICAL Phase-3 consistency (wrong-number-on-a-page) sub-finding: the one genuine
money-divergence (W-065/W-276/W-277 calendar-total status set) is the F-004/Q-10 `period_subtotal`
definitional question (the calendar is a calendar-month all-status view by design, a different
concept than the Projected-only pay-period grid subtotal -- the developer's Q-10 answer decides
whether that is a bug or intended). W-277's entries-load SILENT_DRIFT is inherited F-003/F-009,
already CRITICAL there; recorded here as a new consuming path on the "NO selectinload" side. Two new
questions filed: **Q-18** (W-277 month-end period selection), **Q-19** (W-262 RECEIVED hard-delete
gap).

**P3-cmp-3 verification (a-g):**

- **(a) Row count == triage NEEDS-COMPARISON count for this cluster.** Triage residual:
  **40**; rows produced: **40** (calendar_totals 21 + section8 9 + section5a 10). Every W-NN
  from the `03_consistency.md:4531` cluster definition appears exactly once; the enumerated
  lists reconcile to 40. The triage-table rows for all 40 were re-read this session and
  confirmed all classified `NC`. **PASS.**
- **(b) Every verdict cites code Read this session, with the filter-set comparison (both
  sides' file:line) for any aggregate claim.** `calendar_service.py` (517 lines full),
  `budget_variance_service.py:1-405`, `dashboard_service.py:99-198`, `analytics.py:107-168,
  311-428`, `recurrence_engine.py:610-669`, `transactions.py:388-467,533-622`,
  `archive_helpers.py` (132 full), `templates.py:561-637`, `transfers.py:596-670`,
  `accounts.py:596-741`, `categories.py:178-205`, `grid.py:279-346`, `_calendar_month.html`
  (130 full), `calendar.js` (62 full) -- all Read at source THIS session. Every aggregate
  row (W-065/276/277/281/282/288) cites the calendar/variance/bill filter set AND the grid
  D1 / F-027-S10 canonical with file:line on both sides. No verdict inherited from a plan's
  `planned-per-plan` self-report. **PASS.**
- **(c) Every wrong-number finding (filter divergence / is_deleted omission /
  display-vs-grid mismatch) has a worked example with the dollar delta.** Worked example A
  (W-065 status-set, delta income `+$2,000` / expense `+$120` / net `+$1,880`); worked
  example B (W-277 entries-load, delta `$462.34`). is_deleted omission: none (proven, not a
  vacuous claim -- each aggregate's is_deleted filter cited). **PASS.**
- **(d) Every Jinja financial-arithmetic claim recorded per section 1.3 with the template
  file:line and cross-ref to P1-d's template inventory.** Result: ZERO Jinja financial
  arithmetic in `_calendar_month.html` (full Read, format-strings only) -- cross-referenced
  to P1-d `01_inventory.md:2450` (1.3.12, arithmetic=NO) and `:2686` (1.4 calendar.js,
  arithmetic=NO). The affirmative compliance is recorded in W-064/070/073 and the escalation
  decision (iii). **PASS.**
- **(e) Every PLAN_DRIFT-STRUCTURAL row states the proof the number matches the canonical
  producer (not "appears display-only").** No row is verdicted PLAN_DRIFT-STRUCTURAL-by-
  supersession (the P3-cmp-1 reflex is explicitly NOT used). W-281/W-282/W-288 are HOLDS
  *because the filter set was proven equal to the canonical with both-sides file:line*
  (F-027 S10 / grid D1), not "display-only." W-065's number divergence is escalated with
  example A and gated UNKNOWN-Q-10 (not waved through). **PASS.**
- **(f) Self-spot-check: 6 random verdicts re-Read (>=3 touching a filter-set/number
  comparison).**
  1. **W-065 HOLDS+escalated** (filter-set) -- re-Read `analytics.py:413-426`
     (sum income/expense added to day dict) + `calendar_service.py:225-235` (no status
     filter), `:255` (effective_amount) vs `grid.py:269,274` (Projected-only, effective).
     Status-set divergence confirmed; example A delta re-derived. Confirmed.
  2. **W-281 HOLDS** (filter-set) -- re-Read `budget_variance_service.py:287-294`
     (`is_deleted.is_(False)` + `~status_id.in_(excluded)`) and `:207-210`
     (`excluded=[CREDIT,CANCELLED]`); matches F-027 S10 / effective_amount tiers 1-2.
     Confirmed.
  3. **W-288 HOLDS** (filter-set) -- re-Read `dashboard_service.py:143-146` (Projected +
     expense + is_deleted) and `:191` (`amount=txn.effective_amount`); matches grid D1.
     Confirmed.
  4. **W-277 UNKNOWN-Q-18 + SILENT_DRIFT** (number) -- re-Read `calendar_service.py:461-466`
     (period select `end_date <= last_day`), `:471-480` (NO selectinload), `:482-489`
     (calculate_balances), `:449-450` (anchor-None). All three divergences confirmed;
     example B `$462.34` delta re-derived. Confirmed.
  5. **W-262 HOLDS+Q-19** -- re-Read `archive_helpers.py:29-37` (`[DONE,SETTLED]` +
     is_deleted) vs `transactions.py:535` (income->RECEIVED) vs `ref_seeds.py:79-84`
     (Received is_settled=True) vs `templates.py:616-618` (unconditional delete). RECEIVED
     gap confirmed; code matches literal "Paid or Settled" (HOLDS) + Q-19 raised. Confirmed.
  6. **W-074 HOLDS** -- re-Read `_calendar_month.html:97-99` (`entry.is_paid` badge) +
     `calendar_service.py:261` (`is_paid = bool(txn.status and txn.status.is_settled)` --
     flag-based). Confirmed.
  **Spot-check pass rate: 6/6** (4 of which touch a filter-set/number comparison: W-065, W-281, W-288,
  W-277). No 2+ failures; stop-condition not triggered.
- **(g) Every UNKNOWN names the blocking Q.** W-065 canonical-match axis: **UNKNOWN-Q-10**
  (F-004 `period_subtotal`, `09_open_questions.md:373-421`). W-277 period-selection axis:
  **UNKNOWN-Q-18** (filed this session, `09_open_questions.md`). W-262 RECEIVED axis:
  **UNKNOWN-Q-19** (filed this session). W-082/083/084: **N/A-TEST-CLAIM** (Phase-7 scope,
  not a blocked-on-Q verdict -- a category exclusion, stated as such). **PASS.**

P3-cmp-3 complete: 40 calendar/analytics/deletion verdicts (**36 HOLDS** incl. W-065 and W-262 whose
structural plan claims hold with escalated sub-findings; **1 UNKNOWN-Q-18** W-277 period-selection
[also escalated SILENT_DRIFT via F-003/F-009 + Q-16]; **3 N/A-TEST-CLAIM** W-082/083/084;
**0 VIOLATED, 0 VIOLATED-DEAD, 0 PARTIALLY_HOLDS**; the remaining HOLDS rows carry only (note)s).
The one genuine money divergence -- the calendar-total all-status set vs the Projected-only grid
subtotal (W-065/W-276/W-277-face) -- is the F-004 `period_subtotal` Definition-3-vs-Definition-1
question, canonical-match gated **UNKNOWN-Q-10**, divergence facts + worked dollar delta recorded
regardless. W-277 additionally inherits the F-003/F-009 entries-load **SILENT_DRIFT** (worked
example B, `$462.34` delta) -- a NEW consuming path on the symptom-#1 "NO selectinload" side -- and
the Q-16 anchor-None **SCOPE_DRIFT**. `budget_variance_service` and `_get_upcoming_bills` filter
sets were PROVEN to match the canonical (F-027 S10 / grid D1); no `is_deleted` double-count and no
Jinja financial arithmetic exist in this cluster (P1-d 1.3.12/1.4 cross-confirmed). Two new
questions filed: **Q-18** (W-277 month-end period selection), **Q-19** (W-262 RECEIVED-only income
template silent hard-delete data-loss). Phase 3 is **NOT** complete -- P3-cmp-5 (ops/audit/misc),
then **P3-reconcile** (the only session that may declare Phase 3 complete) remain. F-001..F-056 /
triage table / P3-cmp-1 / P3-cmp-2 / P3-cmp-4 subsections / `Finding IDs used` header unmodified
(append-only). No source, test, or migration file modified. Not committed; developer reviews between
sessions.

#### P3-cmp-5 verdicts: ops/audit/config

Session P3-cmp-5, 2026-05-16. FINAL plan-vs-code comparison sub-session. Fresh-code-read: each of
the 28 W-NN was located and Read at source THIS session -- `auth_helpers.py` (416 lines full),
`account_resolver.py:38-132` (`resolve_grid_account`/ `resolve_analytics_account`),
`savings_dashboard_service.py:70-296,520-549`, `dashboard_service.py:120-175`,
`calendar_service.py:200-237`, `budget_variance_service.py:230-298`,
`loan_payment_service.py:185-228`, `carry_forward_service.py:55-74,210-364,479`,
`retirement_gap_calculator.py` (137 full), `retirement_dashboard_service.py:350-429`,
`recurrence_engine.py:468-492`, `balance_calculator.py:49-109,176`,
`audit_infrastructure.py:146-245`, `scripts/audit_cleanup.py:1-40`, `scripts/seed_user.py:7-91`,
`app/config.py:48-81`, `app/routes/loan.py:109-110`, model `user_id` columns (`account.py:42`,
`transaction_template.py:39`, `transfer.py:92`, `salary_profile.py:43`, `pay_period.py:25`).
Append-only; F-001..F-056, the `Finding IDs used` header, the triage table, and the P3-cmp-1 /
P3-cmp-2 / P3-cmp-3 / P3-cmp-4 subsections are untouched. No source/test/migration modified.

**Triage residual reconciliation.** Cluster definition (`03_consistency.md:4533`): P3-cmp-5 =
phase8_hardening (10) + phase8b (3) + prod_impl (2: W-183,185) + prod_readiness_v1 (9:
W-186,189,190,191,193,194,195,196,197) + net_worth_amort (1: W-169) + req_v3_addendum (2: W-207,215)

+ test_remediation (1: W-303). The 28 W-NN, enumerated:

- phase8_hardening (10): W-170, W-171, W-172, W-173, W-174, W-175, W-176, W-177, W-178,
  W-179.
- phase8b (3): W-180, W-181, W-182.
- prod_impl (2): W-183, W-185.
- prod_readiness_v1 (9): W-186, W-189, W-190, W-191, W-193, W-194, W-195, W-196, W-197.
- net_worth_amort (1): W-169. req_v3_addendum (2): W-207, W-215. test_remediation (1):
  W-303.

**Triage residual for this cluster: 28; rows produced: 28** (10 + 3 + 2 + 9 + 1 + 2 + 1 = 28,
reconciled). The triage-table rows for all 28 (`03_consistency.md:4268-4402`) were re-read this
session and confirmed all classified `NC`.

**Scope framing (the carve-out, mandated).** Audit-plan section 0 excludes routing, auth, audit-log
forensics, observability events, retention scripts, and config from "calculations of money." The
IN-FINANCIAL carve-out for this cluster is: (i) any claim about `user_id` scoping on
`budget.*`/`salary.*` queries -- a missing filter is an IDOR that mixes financial data across users
(CRITICAL); (ii) any claim that gates a money value's correctness. Every OUT-OF-SCOPE row below
carries a one-line code-cited justification (what the code does, why it is not a money computation),
NOT a plan-name assumption. Forensic-gap notes flag any out-of-scope mechanism whose failure would
leave a financial mutation unrecorded.

| W-NN | plan | claim (one line) | scope | code location Read (file:line) | verdict | classn if drift | evidence (file:line) | cross-ref |
| ---- | ---- | ---- | ---- | ---- | ---- | ---- | ---- | ---- |
| W-169 | net_worth_amort | `calculate_balances_with_amortization` must remain in balance_calculator.py + 17 tests | OUT-OF-SCOPE (test-retention/Phase-7 + liveness) | `balance_calculator.py:176`; `tests/.../test_balance_calculator_debt.py:14,97..389`; `year_end_summary_service.py` (grep) | HOLDS | -- | Function present `balance_calculator.py:176`; grep this session: ZERO `app/` callers, the SOLE refs are the named test file (`test_balance_calculator_debt.py:14` import, exercised `:97,135,162,217,307,389`). The callerless state is the PLAN-INTENDED outcome of net_worth_amort W-153 (remove the net-worth fallback caller) -- confirmed: zero `calculate_balances_with_amortization` refs remain in `year_end_summary_service.py`. NOT VIOLATED-DEAD: W-169 (G Downstream) explicitly retains the fn as a tested library utility while W-153 removes its only caller. Exact "17" count is Phase-7's domain (like P3-cmp-3 W-082/083/084). | W-153, Phase 7 |
| W-170 | phase8_hardening | audit_log records row-level INSERT/UPDATE/DELETE with old/new JSONB | OUT-OF-SCOPE (PG forensic trigger, not a money computation) | `app/audit_infrastructure.py:146-148,198-245` | OUT-OF-SCOPE | -- | `audit_log` cols `old_data JSONB :146`, `new_data JSONB :147`, `changed_fields TEXT[] :148`; `audit_trigger_func` DELETE->old `:198-206`, INSERT->new `:209-217`, UPDATE->both `:220-245`. A PL/pgSQL forensic capture trigger; records state, computes no money. **Forensic-gap note:** this trigger is the project's sole tamper-resistant record of `budget.*`/`salary.*` mutations (CLAUDE.md); a gap on an audited table = an unrecorded financial mutation -- Phase-8 awareness. | W-181, W-182, Phase 8 |
| W-171 | phase8_hardening | audit_log captures only changed fields for UPDATE, not whole row | OUT-OF-SCOPE (forensic diff, not money) | `app/audit_infrastructure.py:225-233` | OUT-OF-SCOPE | -- | UPDATE branch builds `v_changed` from `jsonb_each(v_new_data) WHERE NOT v_old_data ? key OR v_old_data->key IS DISTINCT FROM v_new_data->key` `:227-233`; `changed_fields` column populated from it `:243`. JSONB symmetric-diff for the forensic trail; no money figure. **Forensic-gap note:** same trail as W-170. | W-170, W-180, Phase 8 |
| W-172 | phase8_hardening | audit trigger captures app-level user_id from `app.current_user_id` | OUT-OF-SCOPE (forensic attribution, not money) | `app/audit_infrastructure.py:193` | OUT-OF-SCOPE | -- | `v_user_id := current_setting('app.current_user_id', true)::INTEGER` `:193`, written to every audit row `:206,217,245`. Session-local user attribution for forensics; computes no money. **Forensic-gap note:** if unset, financial-mutation rows are attributed NULL (orphaned forensic rows, CLAUDE.md) -- Phase-8 awareness. | W-170, Phase 8 |
| W-173 | phase8_hardening | transaction CRUD logged as structured JSON events | OUT-OF-SCOPE (observability, not money) | `transaction_service.py:155`; `app/utils/log_events.py:408` | OUT-OF-SCOPE | -- | `log_event(...)` (the structured-JSON primitive, `log_events.py:408`) emitted after the txn mutation, e.g. `transaction_service.py:155`. Logs an event record; does not compute or alter any Decimal. Audit-plan section 0 excludes observability events. | W-174, W-177 |
| W-174 | phase8_hardening | transfer-create logged as structured JSON events | OUT-OF-SCOPE (observability, not money) | `transfer_service.py:424`; `log_events.py:408` | OUT-OF-SCOPE | -- | `log_event(...)` at `transfer_service.py:424` after `create_transfer` builds the shadow pair. Observability record post-mutation; no money computation. | W-173, F-029 |
| W-175 | phase8_hardening | anchor-balance-update logged as structured JSON events | OUT-OF-SCOPE (observability, not money) | `app/utils/log_events.py:408` (structured-log primitive) | OUT-OF-SCOPE | -- | `log_event` (`log_events.py:408`) is the app-wide structured-JSON observability primitive; an anchor-update log line records the event, it does not compute the anchor balance (that is `accounts` route + balance_calculator, F-001/Phase-4). Plan source "unspecified" (priors `00_priors.md:422`). Not a money computation. | W-185, Phase 4 |
| W-176 | phase8_hardening | carry-forward logged as structured JSON events | OUT-OF-SCOPE (observability, not money) | `app/utils/log_events.py:408` | OUT-OF-SCOPE | -- | Same `log_event` observability primitive (`log_events.py:408`); a carry-forward event line is emitted post-mutation by the carry-forward path (W-190..194 own the money mechanic). Logging records, does not compute. | W-190, W-177 |
| W-177 | phase8_hardening | recurrence-regeneration logged as structured JSON events | OUT-OF-SCOPE (observability, not money) | `recurrence_engine.py:171` | OUT-OF-SCOPE | -- | `log_event(logger, logging.INFO, EVT_RECURRENCE_GENERATED, BUSINESS, ...)` `recurrence_engine.py:171` after generation. Observability record; the generated txns' money is owned elsewhere. Not a money computation. | W-173, W-303 |
| W-178 | phase8_hardening | audit_log rows older than retention (default 365d) deleted by script | OUT-OF-SCOPE (ops retention script, not money) | `scripts/audit_cleanup.py:4,11-12,17` | OUT-OF-SCOPE | -- | "Deletes audit_log rows older than the configured retention period ... default: AUDIT_RETENTION_DAYS env var, or 365" `:4,11-12`; `--days N`/`--dry-run` flags `:17`. An ops cleanup script over the forensic table; touches no `budget.*`/`salary.*` money column. | W-170, Phase 8 |
| W-179 | phase8_hardening | EVERY budget.*/salary.* (+auth.user_settings/mfa) query must filter user_id | **IN-FINANCIAL** (IDOR carve-out) | proof block below; `auth_helpers.py:121-263`, `account_resolver.py:48-131`, `savings_dashboard_service.py:78-116`, `calendar_service.py:212-234`, `budget_variance_service.py:241-296`, `loan_payment_service.py:194-214`, `carry_forward_service.py:237-267`, model `user_id` cols | **HOLDS** | -- | **Proven user-scoped across the financial surface (no missing filter found -- verification (d) "explicitly proven" branch).** See "W-179 user-scoping proof" below. `budget.*`/`salary.*` carve-out only; `auth.user_settings`/`auth.mfa_configs` are auth-domain, out of this audit's financial scope (not re-verified here). | W-190, F-012, F-031 |
| W-180 | phase8b | UPDATE skips audit write if no column value changed | OUT-OF-SCOPE (forensic no-op suppression, not money) | `app/audit_infrastructure.py:236-239` | OUT-OF-SCOPE | -- | `IF array_length(v_changed, 1) IS NULL THEN RETURN NEW;` `:236-239` -- comment `:234-235` "Suppress no-op UPDATEs ... add no forensic value". Trigger-level forensic optimisation; no money. **Forensic-gap note:** same trail as W-170 (a genuine change still records). | W-171, Phase 8 |
| W-181 | phase8b | DELETE records the deleted row's data as old_data | OUT-OF-SCOPE (forensic capture, not money) | `app/audit_infrastructure.py:198-206` | OUT-OF-SCOPE | -- | `IF TG_OP = 'DELETE' THEN v_old_data := to_jsonb(OLD)` `:198-199`; `INSERT INTO system.audit_log (... old_data, new_data ...) VALUES (... v_old_data, NULL ...)` `:203-206`. Forensic; no money. **Forensic-gap note:** financial-row deletes recorded here only -- Phase-8. | W-170, Phase 8 |
| W-182 | phase8b | INSERT records the new row's data as new_data | OUT-OF-SCOPE (forensic capture, not money) | `app/audit_infrastructure.py:209-217` | OUT-OF-SCOPE | -- | `ELSIF TG_OP = 'INSERT' THEN v_new_data := to_jsonb(NEW)` `:209-210`; `INSERT ... VALUES (... NULL, v_new_data, NULL ...)` `:214-217`. Forensic; no money. **Forensic-gap note:** financial-row inserts recorded here only -- Phase-8. | W-170, Phase 8 |
| W-183 | prod_impl | carry-forward must filter transactions by scenario_id | **IN-FINANCIAL** (scenario scope gates which money rows move) | `carry_forward_service.py:262`; threaded `:291-292,335-337` | **HOLDS** | -- | `_build_carry_forward_context` query filters `Transaction.scenario_id == scenario_id` `:262` (alongside `pay_period_id == source_period_id :261`, `status_id == projected :263`, `is_deleted False :264`). `scenario_id` is a required param of `carry_forward_unpaid` `:291-292`, passed straight into `_build_carry_forward_context` `:335-337`, sourced from the route (`transactions.py:1165` caller). Cross-scenario contamination prevented as claimed. Per-number consistency owned by F-029 / P3-cmp-2 (Option F); not re-verdicted here. | F-029, P3-cmp-2, W-190 |
| W-185 | prod_impl | `stale_anchor_warning` flag returned when done/received exist post-anchor | OUT-OF-SCOPE (Phase-4 stale-detection; flag is a bool, not money) | `balance_calculator.py:49-51,92,104-109` | HOLDS | -- | Docstring `:49-51` "(balances, stale_anchor_warning) ... True if done/received transactions ..."; `stale_anchor_warning = False :92`; set `True :104` on the post-anchor done/received condition; `return balances, stale_anchor_warning :109`. Production side HOLDS as claimed. Whether anything CONSUMES the flag is Phase-4's stale-drift question (`04_source_of_truth.md`), not Phase-3. | Phase 4, F-001 |
| W-186 | prod_readiness_v1 | test conftest ref data includes the "settled" status to match prod | OUT-OF-SCOPE (test fixture / Phase-7) | `tests/conftest.py:1641`; `app/ref_seeds.py` (canonical seed) | OUT-OF-SCOPE | -- | `conftest.py:1641` resolves `STATUS_SETTLED = ref_cache.status_id(StatusEnum.SETTLED)` -- a successful resolve proves the SETTLED status is present in the seeded ref data the test template clones (canonical seed `app/ref_seeds.py`, per CLAUDE.md test-template build). Test-fixture parity; not a money computation. | W-303, Phase 7 |
| W-189 | prod_readiness_v1 | float conversions in route chart files isolated + inline-commented presentation-only | OUT-OF-SCOPE (E-10 presentation boundary; float at JSON serialization does not alter Decimal calc) | `app/routes/loan.py:109-110`; `app/routes/retirement.py`,`investment.py` (grep) | HOLDS | (1.7.8) plan named auto_loan.py/mortgage.py -> consolidated to loan.py | grep this session: the ONLY route-layer chart float is `loan.py:110` `balances.append(float(row.remaining_balance))`, directly preceded by `:109` `# Presentation boundary: float() for Chart.js JSON serialization.` -- isolated + commented as claimed. `retirement.py`/`investment.py` have ZERO `float(` (Decimals/chart_data_service used). Plan files `auto_loan.py`/`mortgage.py` do not exist (consolidated to `loan.py`; 1.7.8 attribution). The float is at the serialization boundary only; upstream calc stays Decimal -- E-10, no money error. | F-005, W-188(COVERED) |
| W-190 | prod_readiness_v1 | carry_forward_unpaid verifies ownership of BOTH source+target periods, rejects mismatch | **IN-FINANCIAL** (IDOR on a financial mutation) | `carry_forward_service.py:237-243,291-296,335-337` | **HOLDS** | -- | `_build_carry_forward_context`: `source = db.session.get(PayPeriod, source_period_id); if source is None or source.user_id != user_id: raise NotFoundError` `:237-239`; identical for `target` `:241-243`. Called unconditionally as step 1 of `carry_forward_unpaid` (`:335-337`, docstring step 1 `:296`). Both periods ownership-checked; mismatch -> NotFoundError -> route 404. Defense-in-depth even if the route already checked (docstring `:316-317`). LIVE: caller `transactions.py:1165`. | W-183, W-191, F-012 |
| W-191 | prod_readiness_v1 | carry_forward_unpaid skips txns with status done/cancelled/credit | **IN-FINANCIAL** (status filter gates which money rows move) | `carry_forward_service.py:258-263` | **HOLDS** | -- | `_build_carry_forward_context` query `Transaction.status_id == projected_id` (`projected_id = ref_cache.status_id(StatusEnum.PROJECTED)` `:256`, filter `:263`). A Projected-ONLY predicate inherently excludes done/cancelled/credit (and received/settled) -- a superset-correct realisation of "skip done/cancelled/credit". The bulk-UPDATE re-asserts `status_id == projected` to close the F-049 SELECT->flush race (comment `:361-365`). | W-190, W-193 |
| W-193 | prod_readiness_v1 | carry_forward_unpaid skips soft-deleted txns | **IN-FINANCIAL** (is_deleted filter gates which money rows move) | `carry_forward_service.py:258-265` | **HOLDS** | -- | Same query: `Transaction.is_deleted.is_(False)` `:264`. Soft-deleted source rows are excluded from the partition; HOLDS verbatim. | W-191, W-194 |
| W-194 | prod_readiness_v1 | carry_forward_unpaid returns a count == number of txns moved | OUT-OF-SCOPE (return is an int count, not a money figure) | `carry_forward_service.py:344,346,406,424,449,468,479` | HOLDS | -- | `count = 0 :346`; incremented per processed row -- discrete `:406`, transfer `:424`, envelope `:449,468`; `return count :479`; empty-partition short-circuit `return 0 :344`. Docstring `:302-304,322-323` "1 per source row processed (envelope settle=1, discrete=1, each transfer=1)". HOLDS; the value is a count, not money. | W-190, W-191 |
| W-195 | prod_readiness_v1 | DevConfig provides a sensible DATABASE_URL fallback when env var unset | OUT-OF-SCOPE (config/env resolution, not money) | `app/config.py:48-81` | OUT-OF-SCOPE | -- | `_DATABASE_URL_SENTINEL = "REPLACE-ME-WITH-YOUR-POSTGRES-PASSWORD"` `:64`; fallback/sentinel-rejection resolution `:70-81` (callers pass `os.getenv(var_name)` or its fallback; prod-missing branch raises loud). Connection-string config; computes no money. | -- |
| W-196 | prod_readiness_v1 | seed_user rejects passwords <12 chars with error + exit code 1 | OUT-OF-SCOPE (credential script / auth, not money) | `scripts/seed_user.py:7-9,83,91` | OUT-OF-SCOPE | -- | Docstring `:7-9` "Validates that the password is at least 12 characters ... Exits with code 1 if the password is" too short; `sys.exit(1)` `:83,91`. Credential-seeding script (auth domain, audit-plan section 0 excludes auth); no money. | W-197 |
| W-197 | prod_readiness_v1 | seed_user enforces the same 12-char minimum as registration | OUT-OF-SCOPE (credential script / auth, not money) | `scripts/seed_user.py:7-9,63-65` | OUT-OF-SCOPE | -- | Docstring `:7-9,63-65` "at least 12 characters, matching the minimum enforced by the application's `change_password()` and `register_user()` functions". States parity with the registration flow; auth-domain script, no money. | W-196 |
| W-207 | req_v3_addendum | deduction with target_account_id auto-applied as income to target each paycheck | **IN-FINANCIAL** (feeds a money projection) | `savings_dashboard_service.py:242-254,520-549`; `retirement_dashboard_service.py:353-366`; `year_end_summary_service.py:1944-1982` | **HOLDS** | -- | Deductions with `target_account_id` are loaded per investment account (`savings_dashboard_service.py:242-254` `PaycheckDeduction.target_account_id.in_(inv_account_ids)`; mirror `retirement_dashboard_service.py:357-366`, `year_end_summary_service.py:1972-1982`) and fed via `adapt_deductions` -> `calculate_investment_inputs(deductions=...)` -> `inputs.periodic_contribution` -> growth projection (`savings_dashboard_service.py:520-549`). The deduction IS applied as a per-period income/contribution to the target account. **(note)** it is modeled as a FORWARD PROJECTION contribution, not posted as an actual income Transaction at receipt time; the resulting figure's cross-producer consistency is owned by F-043 (employer_contribution: `:periodic_contribution`->`calculate_employer_contribution`) / F-042 (growth) -- not re-verdicted here. | F-043, F-042 |
| W-215 | req_v3_addendum | monthly income gap = pre_ret_net_monthly - monthly_pension; pre_ret = net_biweekly*26/12 | **IN-FINANCIAL** (money formula) | `retirement_gap_calculator.py:67-89` | **HOLDS** | (note) documented more-correct refinement (plan-text under-specification, not code drift) | `pre_retirement_net_monthly = (net_biweekly_pay * 26 / 12).quantize(TWO_PLACES, ROUND_HALF_UP)` `:68-70` -- the "net biweekly * 26 / 12" claim VERBATIM. Gap `:85-89`: `monthly_income_gap = max(pre_retirement_net_monthly - effective_pension, ZERO)` where `effective_pension = after_tax_monthly_pension if ... is not None else monthly_pension_income` `:85`. Default path (no `estimated_tax_rate`) == the literal claim. **(note)** when a tax rate is supplied the subtrahend is the AFTER-TAX pension (documented `:83-85` "apples-to-apples ... with net (post-tax) current income") and a `max(...,0)` floor is applied -- both deliberate, in-code-documented correctness refinements of an under-specified plan claim (same more-correct-than-claim precedent as P3-cmp-3 W-278 / P3-cmp-1 W-030/W-115), recorded not escalated, no Q. F-049/F-050 verified only pension pass-through; the gap subtraction is verified HERE. | F-049, F-050, F-035, F-037 |
| W-303 | test_remediation | recurrence interval_n=0 must raise ValueError OR guard against infinite loop / not hang/crash | OUT-OF-SCOPE (recurrence robustness guard, not a money computation; no F-NN audits recurrence_engine) | `recurrence_engine.py:475-478` | HOLDS | -- | `EVERY_N_PERIODS` branch: `n = rule.interval_n or 1` `:476` coerces `0`/`None` -> `1`; `return [p for p in candidates if (p.period_index - offset) % n == 0]` `:478`. With `n` floored to 1, `% n` can never be `% 0` (no ZeroDivisionError/crash) and the match is a finite list-comprehension over `candidates` (no loop -> cannot hang). The claim's "guard against infinite loops / not crash" branch is satisfied. Robustness guard; computes no money figure. | W-177 |

**W-179 user-scoping proof (verification (d): the "explicitly proven" branch).** The financial scope
of W-179 is `budget.*`/`salary.*` queries. Proven structurally + by source read this session that
every such query reaches the DB user-scoped:

1. **Models with a direct `user_id` column** -- `Account` (`account.py:42`),
   `TransactionTemplate` (`transaction_template.py:39`), `Transfer` (`transfer.py:92`),
   `SalaryProfile` (`salary_profile.py:43`), `PayPeriod` (`pay_period.py:25`). Service
   queries on these filter it directly: `savings_dashboard_service.py:80`
   (`Account ... filter_by(user_id=user_id, is_active=True)`), `:258-260`
   (`SalaryProfile filter_by(user_id=user_id ...)`), `account_resolver.py:62-66,72-76,
   122-131` (all `filter_by(user_id=user_id ...)`),
   `retirement_dashboard_service.py:357-360` (`SalaryProfile.user_id == user_id`).
2. **`Transaction` has NO `user_id` column** -- scoped transitively, two independent ways
   (usually both): (a) `pay_period_id.in_(period_ids)` where `period_ids` derive from
   `pay_period_service.get_all_periods(user_id)` / `get_overlapping_periods(user_id, ...)`
   (user-scoped period sets): `savings_dashboard_service.py:87-100`,
   `calendar_service.py:212-234`, `budget_variance_service.py:274-296`,
   `dashboard_service.py:122-149`; and/or (b) `account_id == <ownership-checked account>` --
   the account is resolved via `resolve_analytics_account` (IDOR check
   `account_resolver.py:115-119`: `if acct and acct.user_id == user_id and acct.is_active:
   return acct; return None`), `resolve_grid_account` (`:48-76`, every branch gated
   `acct.user_id == user_id`), or route-layer `get_or_404`/`@require_owner`
   (`auth_helpers.py:121-178`; `savings.py:108-126`, `grid.py:166,182`,
   `analytics.py:96,127` + `resolve_analytics_account`).
3. **`TransactionEntry` / `Entry`** (no `user_id`) -- scoped via `Transaction` ->
   `PayPeriod.user_id` (`entry_service.resolve_owner_id` ownership; P1 inventory
   `01_inventory.md:1504-1507`).
4. **Mutation entry points re-assert ownership at the service boundary**
   (defense-in-depth): `carry_forward_service._build_carry_forward_context:237-243` (both
   periods `user_id != user_id -> NotFoundError`); `transfer_service._get_owned_*`
   (P1 inventory `01_inventory.md:1400`); `auth_helpers.get_owned_via_parent:181-263`
   (Transaction -> PayPeriod.user_id).

Conclusion: no `budget.*`/`salary.*` query was found that reaches the DB without a user scope
(direct `user_id`, user-derived `period_ids`, or an ownership-checked `account_id`). No unscoped
financial query -> **no CRITICAL IDOR escalation**; W-179 HOLDS for the financial carve-out.
`auth.user_settings`/`auth.mfa_configs` are auth-domain and out of this audit's financial scope (not
re-verified; an auth-IDOR concern, not a money figure).

**Escalation decision.** The cluster escalates ONLY (i) a genuine missing-`user_id` filter on a
financial table (CRITICAL + exact query), or (ii) an in-scope money claim VIOLATED with a
wrong-number consequence (worked example). Walked every row:

- (i) **Missing user_id filter: NONE.** W-179 proven user-scoped across `budget.*`/
  `salary.*` (proof block above); W-190 both carry-forward period-ownership checks present
  (`carry_forward_service.py:237-243`). No unscoped financial query found. No CRITICAL.
- (ii) **VIOLATED in-scope money claim: NONE.** Every IN-FINANCIAL row (W-179, W-183,
  W-190, W-191, W-193, W-207, W-215) verdicts HOLDS, verified at source on both sides.
  W-215's after-tax/zero-floor refinement is a documented more-correct deviation from
  under-specified plan text (recorded, not drift; no wrong-number-vs-intent -- the code
  produces a MORE correct gap); W-207's projection-vs-posted-income nuance is owned by
  F-042/F-043. No worked-example escalation required.
- **Forensic-gap notes (OUT-OF-SCOPE, Phase-8 awareness):** W-170/171/172/180/181/182 --
  the `system.audit_trigger_func` is the sole tamper-resistant forensic record of
  `budget.*`/`salary.*` mutations (CLAUDE.md "Audit Triggers"); a missing trigger on an
  audited table, or an unset `app.current_user_id`, leaves a financial mutation
  unrecorded/unattributed. Forensic completeness is gated by `AUDITED_TABLES` +
  the entrypoint trigger-count health check (CLAUDE.md); flagged for Phase-8, NOT a
  Phase-3 money divergence.

No new open question filed (highest remains **Q-19**): every W-NN resolved HOLDS or OUT-OF-SCOPE; no
UNKNOWN, no two-plausible-behaviors ambiguity surfaced (W-215 is a documented refinement, not an
ambiguity).

**P3-cmp-5 verification (a-f):**

- **(a) Row count == triage NEEDS-COMPARISON count for this cluster.** Triage residual:
  **28**; rows produced: **28** (phase8_hardening 10 + phase8b 3 + prod_impl 2 +
  prod_readiness_v1 9 + net_worth_amort 1 + req_v3_addendum 2 + test_remediation 1). Every
  W-NN from the `03_consistency.md:4533` cluster definition appears exactly once; the
  enumerated lists reconcile to 28; the triage rows (`:4268-4402`) re-read, all `NC`.
  **PASS.**
- **(b) Every OUT-OF-SCOPE row has a one-line code-cited justification (not plan-name
  assumed).** All 17 OUT-OF-SCOPE-scope rows cite the actual file:line and state what the
  code does + why it is not a money computation (audit-infra trigger SQL `:146-245`,
  retention script `:4-17`, conftest `:1641`, config `:48-81`, seed_user `:7-91`,
  log_event primitive `log_events.py:408` + representative call sites, recurrence guard
  `:476`, balance_calculator stale flag `:92-109`, loan.py float `:109-110`,
  balance_calculator fn `:176`). None inferred from the plan title. **PASS.**
- **(c) Every IN-FINANCIAL row fully verified at source on both sides.** W-179 (proof
  block: 4 scoping mechanisms, model cols + service queries + resolver IDOR check, both
  sides cited); W-183 (filter `:262` + threading `:291-292,335-337` + route caller
  `transactions.py:1165`); W-190 (`:237-243` ownership + `:335-337` call + LIVE caller);
  W-191 (`:256,263` projected-only) / W-193 (`:264` is_deleted); W-207 (load
  `:242-254` + feed `:520-549` + mirrors) ; W-215 (formula `:68-70` + gap `:85-89`,
  cross-ref F-049/F-050 stated). **PASS.**
- **(d) Missing-user_id-filter escalated CRITICAL with the exact query, OR explicitly
  proven user-scoped.** Taken the "explicitly proven" branch: the W-179 proof block
  enumerates the 4 user-scoping mechanisms with file:line and concludes no unscoped
  `budget.*`/`salary.*` query exists. No CRITICAL. **PASS.**
- **(e) Self-spot-check: 5 random verdicts re-Read (>=2 OUT-OF-SCOPE, >=1 IN-FINANCIAL).**
  1. **W-179 IN-FINANCIAL HOLDS** -- re-Read `account_resolver.py:115-119`
     (`acct.user_id == user_id ... else return None` IDOR check) +
     `savings_dashboard_service.py:80,95` (Account `filter_by(user_id)`, Transaction
     `pay_period_id.in_(period_ids)` from user-scoped `get_all_periods(user_id) :87`).
     Scoping confirmed both sides. Confirmed.
  2. **W-215 IN-FINANCIAL HOLDS+note** -- re-Read `retirement_gap_calculator.py:68-70`
     (`net_biweekly_pay * 26 / 12` quantized) and `:85-89` (`max(pre_ret - effective_pension,
     ZERO)`, `effective_pension` after-tax when tax rate set). Base formula verbatim;
     refinement documented `:83-85`. Confirmed.
  3. **W-180 OUT-OF-SCOPE** -- re-Read `audit_infrastructure.py:236-239`
     (`IF array_length(v_changed,1) IS NULL THEN RETURN NEW;` no-op UPDATE suppression,
     comment `:234-235`). Forensic trigger, not money; justification holds. Confirmed.
  4. **W-194 OUT-OF-SCOPE HOLDS** -- re-Read `carry_forward_service.py:346`
     (`count = 0`), `:479` (`return count`), increments `:406,424,449,468`. Return is an
     integer count, not money; HOLDS. Confirmed.
  5. **W-189 OUT-OF-SCOPE HOLDS** -- re-Read `loan.py:109-110` (`# Presentation boundary:
     float() for Chart.js JSON serialization.` then `float(row.remaining_balance)`);
     grep retirement.py/investment.py zero `float(`. E-10 boundary, plan files
     consolidated to loan.py (1.7.8). Confirmed.
  **Spot-check pass rate: 5/5** (3 OUT-OF-SCOPE: W-180/W-194/W-189; 2 IN-FINANCIAL: W-179/W-215). No
  2+ failures; stop-condition not triggered.
- **(f) Every UNKNOWN names the blocking Q.** ZERO UNKNOWN verdicts in this cluster (all
  HOLDS or OUT-OF-SCOPE) -- vacuously satisfied; no Q-NN gating any row. **PASS.**

P3-cmp-5 complete: 28 ops/audit/config verdicts (**11 HOLDS** -- W-169, W-179, W-183, W-185, W-189,
W-190, W-191, W-193, W-194, W-207, W-215; **17 OUT-OF-SCOPE** -- the audit-infra / observability /
retention / config / credential-script / test-fixture rows, each code-cited;
**0 VIOLATED, 0 VIOLATED-DEAD, 0 PARTIALLY_HOLDS, 0 UNKNOWN**). The IN-FINANCIAL carve-out (W-179
user-scoping; W-183/190/191/193 carry-forward scope/ownership; W-207 deduction-contribution; W-215
income-gap formula) was fully source-verified both sides and all HOLD.
**No CRITICAL IDOR escalation:** every `budget.*`/`salary.*` query proven user-scoped (direct
`user_id`, user-derived `period_ids`, or ownership-checked `account_id`). Six forensic-gap notes
(W-170/171/172/180/181/182) flag the audit-trigger trail for Phase-8 awareness (still OUT-OF-SCOPE).
No new question (highest remains Q-19). This was the LAST comparison cluster (P3-cmp-1..5 done).
Phase 3 is **NOT** complete -- **P3-reconcile** (the only session that may declare Phase 3 complete)
remains. F-001..F-056 / triage table / P3-cmp-1..4 subsections / `Finding IDs used` header
unmodified (append-only). No source, test, or migration file modified. Not committed; developer
reviews between sessions.

### Phase 3 reconciliation

Session P3-reconcile. Gate-not-new-work: this section declares Phase 3 complete only if Gates A-E
all pass and verification a-e all hold. It is append-only and modifies NONE of F-001..F-056, the
triage table, the P3-cmp-1..5 subsections, or the `Finding IDs used` header. 03_consistency.md was
analyzed mechanically (grep/awk projections + ~25 targeted Reads), never end-to-end.

#### Gate A -- W-NN completeness (PASS)

Mechanical reconciliation of the 375-W universe (priors `00_priors.md:240-244`) against the triage
table (`03_consistency.md:4092-4475`) and the five P3-cmp verdict subsections.

| Check | Result |
| --- | --- |
| Triage rows | 375; W-001..W-375 distinct; **zero duplicate, zero gap** (mechanical `comm` vs `seq 1 375`) |
| Bin tally (triage) | COVERED 180 + SUPERSEDED 24 + NEEDS-COMPARISON 161 + DUPLICATE-OF-0.3 10 = **375** |
| COVERED sub-tally | HOLDS 120 + UNKNOWN 31 + PARTIALLY_HOLDS 18 + VIOLATED-DEAD 8 + VIOLATED 3 = **180** (matches the Bin-counts reconciliation block `:4480`) |
| SUPERSEDED | W-127..W-150 = 24 consecutive (all `envelope_view`, A-02); zero mis-binned VIOLATED |
| DUPLICATE-OF-0.3 | W-118/119/120, W-187, W-192, W-205/206, W-300/301, W-330 = exactly the 10 declared |
| Plan-column cross-check | triage plan tally == priors 0.4 declared per-plan counts, **all 20 plans identical** (independent corroboration that the triage universe is faithful) |
| P3-cmp verdict counts | cmp-1=38, cmp-2=35, cmp-3=40, cmp-4=20, cmp-5=28 -> **161** (== completion-line declarations) |
| 161 NC vs 161 cmp | cmp union = 161 total, 161 distinct; **zero W-NN in two cmp subsections**; cmp set == NC set exactly (`comm` both directions empty) |

Bins sum to 375; the 161 NEEDS-COMPARISON W-NNs are each verdicted exactly once across P3-cmp-1..5
with no overlap and no omission. **Gate A PASSES.**

#### Gate B -- trust-but-verify spot-check (PASS, 15/15)

Section-10.8 applied to Phase 3's own output. 15 samples (9 cmp proportional; 6 F biased to
DIVERGE/symptom). Each cited `file:line` Read at source this session and the verdict / divergence /
HOLDS claim confirmed.

| # | Item | Verdict claimed | Source-confirm | Pass |
| --- | --- | --- | --- | --- |
| 1 | W-019 (cmp1) | VIOLATED / PLAN_DRIFT-STRUCTURAL | `interest_params.py` full-file: cols = id/account_id/apy/compounding_frequency only; **no maturity_date, no term_months**; table `interest_params:20` | PASS |
| 2 | W-016 (cmp1) | PARTIALLY_HOLDS | `savings_dashboard_service.py:208-216` filters `InterestParams` by `account_type.has_interest`, NOT HYSA acct_type_id | PASS |
| 3 | W-106 (cmp2) | HOLDS | `recurrence_engine.py:54` def, `:144-145` amount, `:162` estimated_amount, `:163` is_override=False, `:170` flush | PASS |
| 4 | W-110 (cmp2) | HOLDS | `carry_forward_service.py:273-278` exact three-way partition (transfer / `is_envelope` / discrete) | PASS |
| 5 | W-111 (cmp2) | HOLDS | single `settle_from_entries` def `transaction_service.py:38`; consumers `transactions.py:596` (gated `:592-594`), `carry_forward_service.py:896` | PASS |
| 6 | W-065 (cmp3) | HOLDS + escalated DEFINITION_DRIFT | `analytics.py:413-426` per-day income/expense totals added to each day dict in `_build_calendar_weeks` | PASS |
| 7 | W-277 (cmp3) | UNKNOWN-Q-18 + escalated SILENT_DRIFT + SCOPE | `calendar_service.py:449-450` anchor-None ret 0; `:461-466` last period end<=last_day; `:471-480` **no selectinload(entries)**; all three divergences confirmed | PASS |
| 8 | W-235 (cmp4) | HOLDS | `amortization_engine.py:277-281` pre-origination rate-change filter `if r.effective_date >= origination_date` | PASS |
| 9 | W-179 (cmp5) | IN-FINANCIAL HOLDS | `auth_helpers.py:121` `get_or_404` ownership helper (user_id scoping, IDOR forensic log) -- the centralized carve-out | PASS |
| 10 | F-009 (sym #1) | DIVERGE / SILENT | `grid.py:229` selectinload(entries) PRESENT; `savings_dashboard_service.py:92-100` ABSENT; `balance_calculator.py:353-354` short-circuit, `:383-386` entry formula | PASS |
| 11 | F-013 (sym #2) | DIVERGE / SILENT+DEFINITION+PLAN | `amortization_engine.py:908-910` `n`, `:913` stored principal, `:914` stored rate, `:952-954` site-7 ARM branch | PASS |
| 12 | F-014 (sym #3) | DIVERGE / SOURCE+SCOPE+SILENT | grep: no `current_principal=` settle writer (only model:54 / engine-input:913 / schema:1444,1466); `:977-984` ARM-stored vs fixed-walk branch | PASS |
| 13 | F-026 (sym #4) | DIVERGE / SILENT+PLAN | grep: `arm_first/adjustment_months` consumed by ZERO calc sites (only model:60-61 / `_PARAM_FIELDS` loan.py:670 / schema); `:138-142` shrinking `n`; `:952-954` | PASS |
| 14 | F-037 (CRIT) | DIVERGE / DEFINITION | `calibration_service.py:139-144` flat `gross*ss_rate` NO cap; grep zero `ss_wage_base` in calibration_service; `tax_calculator.py:299-307` cap ENFORCED | PASS |
| 15 | F-042 (CRIT) | DIVERGE / SILENT | `retirement_dashboard_service.py:304` `is None`; `:220` `or "0.04"` truthiness; `:321` `if params and params.assumed_annual_return` truthiness | PASS |

**15/15 PASS, 0 failures** (FAIL threshold 2+). No citation resolved to unexpected code; no verdict
overstated. **Gate B PASSES.**

#### Gate C -- finding-set + symptom-spine consolidation

##### C1 -- consolidated verdict register (Phase 4 / Phase 5 load this)

56 findings: 23 AGREE + 1 AGREE-by-construction (F-053) + 21 DIVERGE + 9 UNKNOWN + 1 DEAD_CODE
(F-040); F-056 splits (`entry_sum_total` AGREE / `entry_remaining` UNKNOWN-Q-08). Non-AGREE findings

+ the non-HOLDS cmp verdicts:

**DIVERGE findings (21)** -- file:line in the named finding:

| F | Concept | Classification | Blocking / governing Q |
| --- | --- | --- | --- |
| F-001 | account_balance | SILENT + SCOPE + SOURCE + PLAN | Q-15 (dispatcher), Q-16 (anchor-None), Q-11 (loan card) |
| F-002 | checking_balance | SILENT | -- (E-04 makes it a finding) |
| F-003 | projected_end_balance | SILENT + SOURCE | Q-11; cross sym #1/#5 |
| F-005 | chart_balance_series | SILENT | -- |
| F-009 | proj_end vs checking (**sym #1**) | SILENT | none to classify (E-04) |
| F-013 | monthly_payment (**sym #2**) | SILENT + DEFINITION + PLAN | Q-09 (site-16 intent); Q-17 (fix shape) |
| F-014 | loan_principal_real (**sym #3**) | SOURCE + SCOPE + SILENT | Q-15; Q-11 |
| F-015 | loan_principal_stored | SOURCE | Q-11; Phase 4 owns AUTHORITATIVE/CACHED |
| F-017 | principal_paid_per_period | SCOPE | inherits A-05/Q-09 |
| F-018 | interest_paid_per_period | DEFINITION | A-06; C-05 |
| F-020 | total_interest | DEFINITION (by design) | A-06; C-05 |
| F-021 | interest_saved | ROUNDING | inherits A-05 |
| F-022 | months_saved | DEFINITION | P2-b override 1.7.4 |
| F-023 | payoff_date | SILENT | inherits A-05 |
| F-026 | 5/5 ARM stability (**sym #4**) | SILENT + PLAN | Q-17 (fix shape; verdict NOT blocked) |
| F-027 | effective_amount | SILENT (+ E-16) | Q-08, Q-14 |
| F-032 | paycheck_gross | DEFINITION | A-01 (`:266` 24-list) |
| F-037 | fica (**CRITICAL**) | DEFINITION | none -- IRS cap is a hard invariant (PA-21) |
| F-042 | growth (**CRITICAL**) | SILENT | none -- PA-04/PA-05 reconciliation |
| F-043 | employer_contribution | SILENT | A-01 caveat |
| F-055 | year_summary_employer_total | SILENT + PLAN | inherits F-043; Q-15 |

**UNKNOWN findings (9 + F-056 sub)** -- each blocked on a real pending Q:

| F | Concept | Blocked on |
| --- | --- | --- |
| F-004 | period_subtotal | **Q-10** |
| F-006 | net_worth | **Q-15** |
| F-007 | savings_total | **Q-15** |
| F-008 | debt_total | **Q-15** (internal stored-vs-engine DIVERGE holds regardless: `savings_dashboard_service.py:840/855` vs `:373`) |
| F-016 | loan_principal_displayed | **Q-11** |
| F-028 | entry-progress cross-anchor | **Q-08** (cross-anchor SILENT DIVERGE holds regardless) |
| F-051 | year_summary_jan1_balance | **Q-15** |
| F-052 | year_summary_dec31_balance | **Q-15** |
| F-054 | year_summary_growth (YG1) | **Q-15** (YG2 AGREE-by-construction) |
| F-056 | entry_remaining (sub) | **Q-08** (`entry_sum_total` sub = AGREE) |

**DEAD_CODE:** F-040 legacy `calculate_federal_tax` (zero `app/` consumers, grep-confirmed).

**Non-HOLDS cmp verdicts (PLAN-DRIFT corpus for Phase 8):**

| cmp | W-NN | Verdict |
| --- | --- | --- |
| cmp-1 | W-019 | **VIOLATED** (Option A CD columns never added; latent missing feature, no wrong number) |
| cmp-1 | W-010/013/014/016/018/021/022/030/040 | **PARTIALLY_HOLDS** x9 (all PLAN_DRIFT-STRUCTURAL: type-ID dispatch superseded by metadata flags; substance holds, zero wrong-number) |
| cmp-1 | W-020 | **N/A-OPTION-NOT-ADOPTED** |
| cmp-2 | W-126 | **PARTIALLY_HOLDS** (HTTP-status deviation only; no dollar figure) |
| cmp-3 | W-277 | **UNKNOWN-Q-18** + escalated entries-load SILENT_DRIFT (inherits F-003/F-009) + SCOPE (Q-16) |
| cmp-3 | W-262 | HOLDS literal + **escalated RECEIVED hard-delete data-loss, axis UNKNOWN-Q-19** |
| cmp-3 | W-065 | HOLDS + escalated filter-set DEFINITION_DRIFT (calendar all-status vs grid Projected-only; F-004) |
| cmp-3 | W-082/083/084 | **N/A-TEST-CLAIM** x3 |
| cmp-4 | -- | 0 non-HOLDS (20/20 HOLDS, money-math hand-verified) |
| cmp-5 | -- | 0 non-HOLDS (11 IN-FINANCIAL HOLDS, 17 OUT-OF-SCOPE; no IDOR escalation) |

##### C2 -- symptom -> root-cause map (each Read-confirmed with a worked example)

| Symptom (developer) | Root-cause finding(s) | Read-confirmed mechanism + worked example |
| --- | --- | --- |
| **#1** $160 grid vs $114.29 /savings, same current period | **F-009** + **W-277** (new consuming path) | F-009: sole pinned dimension is `selectinload(Transaction.entries)` PRESENT `grid.py:229` / ABSENT `savings_dashboard_service.py:92-100`; `_entry_aware_amount` returns the entry formula `balance_calculator.py:383-386` vs `effective_amount` `:353-354`. Worked: gap `$160-$114.29=$45.71` = sum(estimated - max(est-cleared-credit, uncleared)) the entries-unloaded path double-subtracts. **W-277** is the SAME gap on a NEW page: `calendar_service.py:471-480` also omits selectinload -> calendar month-end balance DIVERGES from grid identically. |
| **#2** $1911.54/$1914.34/$1912.94 -> $1910.95 | **F-013** | 16 sites feed `calculate_monthly_payment` incompatible `(principal,rate,n)` triples. Site-7 `:952-954` (stored principal `:913`, stored rate `:914`, calendar `n` `:908-910`) vs site-3 schedule per-row `n=max_months-month_num+1` -> few-dollar gap. Worked: P=400000,r=.065,T=360,e=48 -> site7 n=312 ($2628.xx), site3 n~=311 ($2631.xx). "$1910.95 after editing current principal" = `update_params` setattr `loan.py:672-674` writes stored col, site-7 recomputes lower. |
| **#3** current principal does not move as transfers settle | **F-014** | grep-proven: zero `current_principal=` settle writer; sole writer `update_params` setattr (`loan.py:672-674`). ARM `cur_balance = current_principal` (`amortization_engine.py:978`, STORED) -> never consults confirmed rows. Worked: ARM $312,000 unchanged after N confirmed payments until manual edit; fixed engine-walks but card still renders STORED `loan/dashboard.html:104`. |
| **#4** 5/5 ARM payment fluctuates inside fixed window | **F-026** | `arm_first_adjustment_months` inert (grep zero calc consumers); site-7 re-amortizes FROZEN stored principal (F-014) over STRICTLY-DECREASING `remaining` (`:138-142`). Hand arithmetic: $400k @ 6%/360, months_elapsed 24 -> $2,460.45; month 25 -> $2,463.27 (**+$2.82**), both != correct constant $2,398.20, no rate change, no edit. |
| **#5** /accounts balances match nowhere else | **F-001** + **F-008** | F-001: same `(user,period,scenario,account)` -> grid/dashboard $962.34 (entry formula) vs /savings//accounts/net-worth $500.00 (effective_amount) for one Projected envelope expense, anchor $1000, cleared $462.34; + anchor-None 4-way SCOPE + loan-base 3-way SOURCE + dual dispatch (Q-15). F-008: stored `current_principal` $300,000 debt card vs engine `proj.current_balance` $297,450 account card vs net-worth liability $297,450 -- three figures, one loan, one page. |

All five C2 mappings hold; each named finding was Read in full this session and contains a
source-cited worked example that root-causes the symptom. No mapping failed.

##### C3 -- CRITICAL pre-list for Phase 8 (money impact, one line each)

Severity is a Phase-8 assignment (audit-plan section 8); these are pre-flagged from the
Read-confirmed money impact in the finding bodies.

| Item | Money impact (Read-confirmed) |
| --- | --- |
| **F-037** fica SS-cap bypass | High earner on the calibration path: SS accrues all 26 periods -> year SS $19,344 vs correct $11,439 = **+$7,905/yr FICA overstatement**, net pay understated $744/over-cap period, no error (`calibration_service.py:139-144` has no `ss_wage_base`/cumulative input). |
| **F-042** growth phantom income | Explicit-zero SWR: slider shows 0.00% but gap math uses 4% -> **$4,000/mo phantom retirement income**; zero-return $100k account dropped from denominator -> displayed 7.00% vs true 3.50% weighted return (`retirement_dashboard_service.py:220/:321` truthiness). |
| **Q-19 / W-262** RECEIVED hard-delete | `hard_delete_template` (`templates.py:616-618`) permanently destroys RECEIVED income transaction history because `template_has_paid_history` checks only [DONE,SETTLED]; RECEIVED is `is_settled=True` -> **irreversible settled-income data loss on an explicit user delete**, no wrong number. |
| **W-065 / W-277** calendar drift | Calendar "End Balance" (`calendar_service.py:435-489`) omits `selectinload(entries)` (`:471-480`) -> shows a **different dollar amount than the grid** for the same month with Projected envelope+cleared entries (inherits F-003/F-009); period-selection off-by-one (Q-18); per-day totals use all-status vs grid Projected-only (W-065 DEFINITION_DRIFT, F-004). |
| (symptom CRITICALs) | F-001/F-008/F-009/F-013/F-014/F-026 are the developer-reported wrong-dollar symptoms (C2); CRITICAL by the audit-plan section-8 rubric -- already the Phase-5 spine, listed here for Phase-8 ordering. |

**Gate C PASSES** (C1/C2/C3 delivered; every C2 mapping Read-confirmed with a worked example).

#### Gate D -- E1 completeness (PASS, zero orphan)

The E1 register (`02_concepts.md:3215-3271`) = 41 multi-path + 8 single-path = **49 rows** (47
cataloged + 2 Gate-A orphans). The Phase-3 family-coverage tally (`03_consistency.md:3964-4052`)
maps every E1 row to a finding.

| Reconciliation | Result |
| --- | --- |
| E1 multi-path rows (mechanical count `02_concepts.md:3215-3271`) | 41 -> F-IDs (entry_sum_total + entry_remaining both -> F-056) |
| E1 single-path rows | 8 -> F-024/F-030/F-044/F-045/F-047/F-048/F-049/F-050 |
| Tally finding-ID union | exactly **F-001..F-056, 56 distinct, contiguous, no gap** |
| Tally F-IDs vs actual `## Finding F-` headers | identical both directions (`comm` empty) -- 56 == 56 |
| Standalone section-3.1 findings (no own E1 row, by design) | F-009/F-010/F-011/F-012/F-026/F-028/F-031/F-040 = 8 |
| Orphan E1 rows | **0** |

Every E1 row maps to a finding; the P3-d2 zero-orphan assertion reconciles against the actual 56
F-headers with no collision. **Gate D PASSES.**

#### Gate E -- open-questions blocked register (PASS, no dangling UNKNOWN)

Distinct Q referenced in 03_consistency.md = **Q-08..Q-19** (12, contiguous); all 12 exist as
headers in `09_open_questions.md`; **highest Q = Q-19**; Q-01..Q-07 are the answered/verified
cross-plan + A-01 set. Every UNKNOWN verdict cites a real pending Q (mechanical: 10 F-UNKNOWN lines
L287/364/402/449/1089/2047/3575/3602/3700/3859 each carry a Q within 3 lines; cmp UNKNOWNs
W-277->Q-18, W-262->Q-19). **Zero dangling UNKNOWN.**

| Q | Gates (findings / verdicts) | Developer input that resolves it |
| --- | --- | --- |
| Q-08 | F-028, F-056(`entry_remaining`), F-027 rows S2/S3/R1-R3, F-046 GP2 | estimated_amount vs re-anchor on actual_amount for a settled entry-tracked txn (A-08 proposed) |
| Q-09 | F-013 site-16 *intent* (DIVERGE itself not blocked), inheritors F-017/F-020/F-021/F-023 | `debt_strategy.py:127` unconditional ARM-formula intent; 16-site count (A-09 proposed = 16) |
| Q-10 | F-004 (UNKNOWN), W-065 escalation, F-009/F-002 cross-link | period_subtotal: grid display detail vs shared concept (effective_amount vs _entry_aware_amount canonical) (A-10 proposed) |
| Q-11 | F-016 (UNKNOWN), F-014/F-015/F-001 loan-base | which principal a user-facing page MUST show (stored vs engine-real) (A-11 proposed) |
| Q-12 | F-025 dti context, period_subtotal obligations path | obligations route-resident aggregation SRP; mortgage double-count; 26/12 dup (A-12 proposed) |
| Q-13 | F-034, F-035, F-038 | calibrate_preview inline `gross-total_pre_tax` pct-base bias (A-13 proposed) |
| Q-14 | F-027 R4/R5 (effective_amount write), F-056 `entry_sum_total` | mark_paid vs mark_done settle/auto-settle asymmetry (A-14 proposed) |
| Q-15 | F-001/F-006/F-007/F-008/F-014/F-051/F-052/F-054/F-055, F-042 G4 (largest cluster) | canonical per-account dispatcher (`_compute_account_projections` vs `_get_account_balance_map`); net_worth_amort W-152 catch-up vs superseded |
| Q-16 | F-001/F-003/F-006/F-007 SCOPE axis, W-277 anchor-None | intended no-anchor behavior: blank / $0-anchored / omit account |
| Q-17 | remediation direction of F-013 + F-026 (verdict DIVERGE NOT blocked), ties F-014 | 5/5 ARM site-7: re-amortize engine-real balance vs honor arm_first_adjustment_months vs maintain stored col |
| Q-18 | W-277 verdict (P3-cmp-3) | calendar month-end "last period ending in/after the month" vs current "last completed within month" |
| Q-19 | W-262 escalation (P3-cmp-3) | widen `template_has_paid_history` to `is_settled` / enumerate [DONE,RECEIVED,SETTLED] vs RECEIVED-only history intentionally hard-deletable |

No new gate-surfaced ambiguity (the gate is reconciliation; every divergence already maps to an
existing Q or is a provable-from-code DIVERGE). **Gate E PASSES.**

#### Verification before completion

- **(a)** Gate A arithmetic reconciled and shown -- bins 180+24+161+10=375; cmp 38+35+40+20+28=161
  unique; cmp set == NC set; plan-column == priors 0.4 (20/20). **HOLDS.**
- **(b)** Gate B 15-row spot-check table present; **15/15 PASS** (threshold 2+). **HOLDS.**
- **(c)** Gate C C1/C2/C3 tables present; all five C2 symptom mappings Read-confirmed with
  source-cited worked examples. **HOLDS.**
- **(d)** Gate D zero-orphan reconciliation shown -- 49 E1 rows mapped; tally F-IDs == 56 actual
  F-headers, contiguous, no orphan/collision. **HOLDS.**
- **(e)** Gate E register present; Q-08..Q-19 each gated to findings; every UNKNOWN cites a real
  pending Q; highest Q-19; **zero dangling UNKNOWN.** **HOLDS.**

### Phase 3 complete

Gates A-E all PASS; verification a-e all hold.

- **Gate results:** A PASS (375 reconciled; 161 cmp unique) / B PASS (15/15 spot-check) /
  C PASS (C1 verdict register + C2 symptom spine + C3 CRITICAL pre-list) / D PASS (zero E1
  orphan) / E PASS (no dangling UNKNOWN).
- **Counts:** **56 findings** (F-001..F-056, contiguous, no collision: 23 AGREE + 1
  AGREE-by-construction + 21 DIVERGE + 9 UNKNOWN + 1 DEAD_CODE, F-056 split). **375 W-NN**
  fully binned (180 COVERED / 24 SUPERSEDED / 161 NEEDS-COMPARISON verdicted across
  P3-cmp-1..5 / 10 DUPLICATE-OF-0.3). **12 open questions** (Q-08..Q-19; Q-01..Q-07 the
  answered/verified set).
- **Symptom spine (Phase 5 input):** #1 -> F-009 (+ W-277 new consuming path); #2 -> F-013;
  #3 -> F-014; #4 -> F-026; #5 -> F-001 + F-008. Each Read-confirmed with a worked example.
- **CRITICAL pre-list (Phase 8 input):** F-037 (+$7,905/yr FICA), F-042 ($4,000/mo phantom
  income + weighted-return overstatement), Q-19/W-262 (irreversible RECEIVED income
  data-loss), W-065/W-277 (calendar dollar drift), plus the six symptom findings.
- **Phase 4 (source-of-truth) loads:** C1 SOURCE_DRIFT rows -- F-014/F-015/F-001/F-008
  stored-vs-engine `current_principal`/balance; the anchor stored-column cluster (F-027
  OUT-OF-SCOPE S4/S6/S8/S11); Q-15-blocked dispatcher set. **Phase 5 (symptoms) loads:** the
  C2 map.
- This statement supersedes the P3-cmp-5 "P3-reconcile remains" marker. F-001..F-056, the
  triage table, the P3-cmp-1..5 subsections, and the `Finding IDs used` header are
  unmodified (this section is append-only). No source, test, or migration file modified by
  this session. Not committed; the developer reviews (natural commit+push point is here,
  before Phase 4/5).

**Phase 4 and Phase 5 may begin.**

--- Q-answer reconciliation (2026-05-18) ---

Trigger: every Q this Phase-3 document filed or inherited was adjudicated by
the developer 2026-05-18 and recorded as behavioral expectations E-18..E-28 in
`00_priors.md` 0.3 (A-NN in `09_open_questions.md`). This section flips the
`UNKNOWN-Q-NN` axes and annotates the "DIVERGE...regardless" findings with their
now-determined remediation direction. Additive only: every original finding
verdict and its proven divergence FACTS are unchanged; only the gated axis is
resolved.

Trust-but-verify: re-derived independently from the locked E/A. The prior
ad-hoc reconcile-gate tables in this file (the gate (a)-(g) block, the Non-AGREE
summary, and the cmp-3 tables) were treated as UNVERIFIED and re-checked, not
trusted. No code is re-proved -- the divergence is already proven from source;
only the developer-intent axis flips.

Completeness: the universe is closed by this document's own passed gate (g)
("every UNKNOWN names its blocking Q"). The exhaustive set of gated findings is
enumerated below; each maps to a locked E/A. Items NOT listed had no
answer-dependent axis (their AGREE/DIVERGE verdicts were independent of any Q
and are unaffected).

| Finding | Was (gated axis) | Now | Governing | Note |
|---|---|---|---|---|
| F-004 `period_subtotal` | UNKNOWN (Q-10) | DIVERGE; canonical = single producer, `_entry_aware_amount` base | A-10 -> E-25 | D1-D2/D1-D3 divergence facts already recorded; verdict label flips UNKNOWN->DIVERGE |
| F-006 / F-007 dispatcher | UNKNOWN (Q-15) | DIVERGE vs E-18; W-152 = "code must catch up" (not "plan superseded") | A-15 -> E-18 | multi-aggregator + stored-vs-engine divergence already recorded regardless |
| F-008 `debt_total` | Q-15 (DIVERGE holds regardless) | stays DIVERGE; canonical aggregate base = the single resolver | A-15 -> E-18 | internal `:840/855` vs `:373` inconsistency unchanged |
| F-013 / F-016 `loan_principal_displayed` | UNKNOWN (Q-11) PRIMARY PATH | DIVERGE vs E-18; `current_principal` RETIRED, one resolver | A-11/A-22 -> E-18 | E-04 unlabeled-divergence facts stand |
| F-028 entry-progress | Q-08 (SILENT cross-anchor DIVERGE regardless) | base axis -> AGREE on `estimated_amount` (interpretation 1); cross-anchor amount-vs-remaining inconsistency stays DIVERGE | A-08 -> E-21 | the AGREE is on the base only; the mixed-base row remains a finding (label/reconcile remediation) |
| F-046 `goal_progress` GP2 | UNKNOWN (Q-08) | AGREE (estimated base) | A-08 -> E-21 | GP1 AGREE unchanged |
| F-054 `year_summary_growth` YG1 | UNKNOWN (Q-15) | resolved vs E-18 (single resolver); YG2/YG3 AGREE-by-construction unchanged | A-15 -> E-18 | -- |
| F-055 `year_summary_employer_total` | DIVERGE(F-043) + UNKNOWN (Q-15) | DIVERGE(F-043) stands; the Q-15 component resolved vs E-18 | A-15 -> E-18 | inherited F-043 DIVERGE independent of Q |
| F-056 `entry_remaining` | UNKNOWN-Q-08 (`entry_sum_total` AGREE) | AGREE on `estimated_amount` base | A-08 -> E-21 | split unchanged |
| W-065 `calendar_totals` | UNKNOWN-Q-10 (canonical-match axis) | DIVERGE; canonical = the single subtotal producer; status-set + entry-aware divergence stands | A-10 -> E-25 | HOLDS structural claim unchanged |
| W-277 `_compute_month_end_balance` | UNKNOWN-Q-18 + SILENT_DRIFT + SCOPE(Q-16) | period-selection DIVERGE vs E-27; entries-load SILENT_DRIFT closed by routing through the canonical entries-aware path (E-27); anchor-None resolved by E-19 (state eliminated) | A-18 -> E-27; A-16/A-20 -> E-19 | F-003/F-009 inheritance stands as the entries-load instance |
| W-262 hard-delete RECEIVED axis | UNKNOWN-Q-19 (HOLDS literal + escalated) | critical DIVERGE -- silent irreversible data loss; semantic `is_settled` guard + constrained delete | A-19 -> E-22 | literal-wording HOLDS superseded: the plan's "Paid or Settled" wording itself too narrow |
| F-003 / F-009 anchor-None axis | Q-16 (SCOPE_DRIFT regardless) | DIVERGE vs E-19; the NULL anchor-period state is eliminated (dated anchor, derived period) | A-16/A-20/A-21 -> E-19 | entries-load SILENT_DRIFT half independent, unchanged |

Prior ad-hoc reconcile-gate artifacts in this file (gate (a)-(g), Non-AGREE
summary, cmp-3 tables) are superseded by this section for the gated axes; their
non-gated content is unaffected and was re-checked consistent.

**Phase 5 may consume `03_consistency.md` with every UNKNOWN-Q axis resolved.**
