> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Phase 8 -- Findings report

Read-only synthesis (plan permission mode). Source, tests, migrations,
templates, and static files are untouched; `git status` at session end shows
only `docs/audits/financial_calculations/`.

## Session log

- **P8-a (this session)** -- builds Part 8.A (the cluster skeleton: cluster
  IDs, provisional severity tier, governing E-NN, the full `Subsumes`
  traceability block per cluster -- NO prose, NO remediation sentence, NO
  Evidence element) and the COMPLETE Part 8.B (master reverse-index /
  surjection proof). Severity ordering is provisional and is finalized in P8-e.
  P8-b..P8-d add, per cluster, the remaining schema-3.1 elements
  (severity+rubric justification, category, plain-language paragraph,
  Evidence with the key line re-resolved to live source, phase-doc pointers,
  open questions, remediation-direction sentence, blast radius). P8-e appends
  Part 8.C (the verification gate) and finalizes severity ordering.
- P8-a does NOT write any Evidence element; trust-but-verify contract item 1
  (re-resolve every cited `file:line` to live source with the key line quoted)
  binds P8-b..P8-d, which write Evidence. P8-a re-resolved only the Phase-3
  `F-NN` headers it cites in the traceability blocks (done this session;
  headers at `03_consistency.md` confirmed live).

## Finding-ID scheme and two recorded divergences from the audit plan

- **ID scheme:** `CRIT-NN` / `HIGH-NN` / `MED-NN` / `LOW-NN`, numbered in
  severity order within tier. The audit plan
  (`financial_calculation_audit_plan.md:714`) instructs "ID (F-001,
  F-002, ...)". That collides head-on with Phase 3's live, in-use catalog
  `F-001..F-056` (`03_consistency.md`). Reusing `F-NNN` for Phase-8 findings
  would make every traceability block ambiguous. **Recorded
  audit-plan-vs-execution divergence:** Phase 8 uses the tiered
  `CRIT/HIGH/MED/LOW-NN` scheme; the Phase-3 `F-NN` IDs are referenced only as
  inherited source IDs in the `Subsumes` blocks and Part 8.B. (Reconciliation
  item R-7.)
- **Output path:** the audit plan writes
  `docs/audit/financial_calculations/08_findings.md` (singular "audit",
  `financial_calculation_audit_plan.md:707`, and again at `:1009/:1025`). The
  live audit tree is `docs/audits/financial_calculations/` (plural), where
  `00..07` and `09` already live. **Recorded typo in the audit plan:** the
  singular path is not created; this file is written to the plural path.
  (Reconciliation item R-6.)

## Surjection rule (Part 8.B contract, schema 3.2)

Every section-1 source ID maps to **exactly one** of:

1. a Part-8.A cluster (`-> CRIT/HIGH/MED/LOW-NN`); or
2. `NOT-A-FINDING: <reason>`, reason drawn from the closed set
   `AGREE | COVERED | AUTHORITATIVE | HOLDS | superseded-by-A-NN |
   resolved-intent-not-a-defect`; or
3. `POINTER -> <cluster>` -- reserved for `PT-01..PT-20`, which section 1 row
   14 defines as "Cross-referenced from the cluster each would lock; **not
   findings themselves**". This third disposition is a deliberate,
   section-1-sanctioned consumption (a proposed test is neither a defect nor a
   non-defect); it is NOT an orphan and is called out here so G4's "zero
   source ID absent / zero mapped twice" still holds with PT-NN explicitly
   accounted.

G4 (re-run mechanically in P8-e): zero section-1 ID absent; zero ID with two
`-> cluster` mappings; every `PA-01..PA-30` present; every Phase-3 DIVERGE and
every Phase-4 UNCLEAR present with a `-> cluster` (never `NOT-A-FINDING`).

---

# Part 8.A -- cluster skeleton

Provisional severity, governing E-NN, and the full `Subsumes` block per
cluster. No prose, no Evidence, no remediation. `(sym #N)` marks a
developer-reported symptom; `(UNK Q-NN)` a Phase-3 UNKNOWN with its blocking
Q; `(UNCLEAR Q-NN)` a Phase-4 UNCLEAR column; verdicts/classifications quoted
from the `03_consistency.md` C1 register (`:5984-6047`) and the
`04_source_of_truth.md` D3 classification table (`:2099-2127`).

## CRITICAL tier (provisional; developer-reported symptoms first per audit-plan section 8)

### CRIT-01 -- No canonical date-anchored, entries-aware balance producer: cross-page and aggregate checking balances diverge

- **Severity + rubric justification:** CRITICAL. Refinement B (the
  developer-reported symptoms #1 and #5), verified this session against a
  cited displayed wrong dollar, not assumed from the audit plan's "likely."
  The current-pay-period checking balance renders as **$160.00** on the grid
  balance row (`grid.py:243-248`, entries eager-loaded `grid.py:229`, entry
  formula `balance_calculator.py:383-386`) and **$114.29** on the `/savings`
  checking tile (`savings_dashboard_service.py:352`, no entries `:92-100`,
  short-circuit `balance_calculator.py:353-354` -> `effective_amount`) for the
  identical account/period/scenario, no error raised. Hand-recomputed this
  session from the `05_symptoms.md:195-281` worked example: anchor $614.29,
  one Projected envelope expense estimated $500.00 with three cleared debit
  entries summing $45.71 -> grid $160.00 vs `/savings` $114.29; the $45.71 gap
  is the already-cleared dollars double-subtracted; the F-009 / F-001 worked
  reconstructions (`03_consistency.md:698-716`, `:190-201`) reproduce the same
  structure. Symptom #5: the same tuple yields $114.29 on `/accounts`
  (`accounts.py:1432`), `/savings`, and the net-worth input
  (`year_end_summary_service.py:2127`) but $160.00 on the grid and the
  dashboard balance card (`dashboard_service.py:349`) -- a wrong dollar on
  pages the developer relies on for budgeting decisions, the divergence not
  visible as an error.
- **Category:** drift, source-of-truth, DRY.
- **Plain-language description:** The app has no single canonical routine
  that answers "what is this account's balance as of this pay period." Five
  pages each build their own transaction query and call the shared balance
  engine, but only some eager-load the per-purchase `TransactionEntry` rows.
  The engine silently behaves two different ways depending on whether those
  rows were loaded: when they are (grid, dashboard) it correctly holds back
  only the not-yet-cleared part of a projected envelope expense; when they
  are not (`/savings`, `/accounts`, net worth, calendar) it holds back the
  full estimate, double-counting purchases that already cleared the bank and
  are therefore already inside the real anchor balance. The same checking
  account, same pay period, same scenario therefore shows one number on the
  grid and dashboard and a different lower number on `/savings`, `/accounts`,
  and net worth, with no error and no label. A second axis: when an account's
  anchor pay period is unset (the default for every newly-registered user)
  the five pages diverge again -- blank row, stored-balance-at-current-period
  projection, or account omitted -- so the account "matches nowhere." This is
  exactly symptom #1 ($160 vs $114.29) and symptom #5 (`/accounts` matches
  nowhere).
- **Subsumes:**
  - Symptom **#1** (`05_symptoms.md:18,283-323`); symptom **#5** primary
    (`05_symptoms.md:1092,1437-1481`; loan-base facet cross-ref CRIT-02).
  - Phase 3: **F-001** account_balance DIVERGE (SILENT+SCOPE+SOURCE+PLAN;
    Q-15/Q-16/Q-11); **F-002** checking_balance DIVERGE (SILENT; E-04);
    **F-003** projected_end_balance DIVERGE (SILENT+SOURCE; Q-11); **F-004**
    period_subtotal UNKNOWN (Q-10); **F-005** chart_balance_series DIVERGE
    (SILENT); **F-006** net_worth UNKNOWN (Q-15); **F-007** savings_total
    UNKNOWN (Q-15); **F-009** proj_end-vs-checking DIVERGE (SILENT, sym #1);
    **F-027** effective_amount DIVERGE (SILENT +E-16; Q-08/Q-14; E-16 facet
    cross-ref MED-04, Q-08 facet cross-ref MED-03); **F-051**
    year_summary_jan1_balance UNKNOWN (Q-15); **F-052**
    year_summary_dec31_balance UNKNOWN (Q-15; balance-as-of-date facet
    cross-ref HIGH-02); **F-054** year_summary_growth YG1 UNKNOWN (Q-15;
    YG2 sub is NOT-A-FINDING:AGREE-by-construction, Part 8.B).
  - Phase 4: `budget.accounts.current_anchor_balance` AUTHORITATIVE;
    `budget.accounts.current_anchor_period_id` **UNCLEAR (Q-20)**;
    `budget.account_anchor_history.anchor_balance` CACHED.
  - Phase 6: **D6-02** (E-19, no single anchor resolver); **D6-03** (E-25, no
    single period-subtotal producer); **D6-06** (E-25 family, `_sum_remaining`
    / `_sum_all` byte-identical bodies); **D6-08** (E-25 family, hand-rolled
    `effective_amount` 2-tier mirror in 5+ sites).
  - Phase 7: BLOCKED-ON-OPEN-QUESTION / PRODUCER-UNKNOWN verdicts for
    `account_balance`, `projected_end_balance`, `period_subtotal`,
    `net_worth`, `savings_total`, `chart_balance_series`,
    `year_summary_jan1/dec31_balance`, `year_summary_growth`.
  - Prior-audit: **PA-03** (grid.balance_row `scenario.id` None-deref 500);
    **PA-09** (balance-calc done/received post-anchor caveat).
  - Resolved intent (governing end state, not re-litigated): Q-10/A-10 (E-25),
    Q-15/A-15 (E-18), Q-16/A-16 (E-19), Q-20/A-20 (E-19).
  - PT pointers (Part 8.B): PT-01, PT-04, PT-05.
- **Governing E-NN:** E-25 (one entries-aware period-subtotal/balance
  producer) + E-19 (one date-anchored anchor resolver); E-04 prior; A-15
  (Q-15) resolved by E-18 for the canonical-aggregate question.
- **Evidence (re-resolved to live source this session, key line quoted):**
  - `app/routes/grid.py:229` -- `selectinload(Transaction.entries),`
    (entries eager-loaded; `:438` HTMX `_balance_row` partial carries the
    same `.options(selectinload(Transaction.entries))`); anchor at `:238`
    `account.current_anchor_balance if account else Decimal("0.00")`;
    `calculate_balances` call `:243-248`.
  - `app/services/balance_calculator.py:353-354` --
    `if 'entries' not in txn.__dict__:` / `return txn.effective_amount`
    (the silent degrade); entry formula `:383-386`
    `return max(txn.estimated_amount - cleared_debit - sum_credit,
    uncleared_debit,)`; anchor-period branch `:72-75`
    `if period.id == anchor_period_id:` / `running_balance = anchor_balance
    + income - expenses`.
  - `app/services/savings_dashboard_service.py:92-100` -- the preload query
    has **no** `.options(selectinload(Transaction.entries))`; `:352`
    `current_bal = balances.get(current_period.id) if current_period else
    anchor_balance`.
  - `app/routes/accounts.py:1407-1416` -- query has **no**
    `selectinload(entries)`; `:1432` `current_bal =
    balances.get(current_period.id) if current_period else anchor_balance`.
  - `app/services/year_end_summary_service.py:2065-2066` --
    `if account.current_anchor_period_id is None:` / `return None`;
    `:2085-2094` query has **no** `selectinload(entries)`; `:2079-2081`
    loan branch uses `_schedule_to_period_balance_map(...)`.
  - `app/models/transaction.py:245` -- `return self.actual_amount if
    self.actual_amount is not None else self.estimated_amount` (the value
    the entries-unloaded short-circuit returns for a Projected expense).
  - `app/services/calendar_service.py:471-480` (W-277 entries-load
    instance, primary home HIGH-02) -- query has **no**
    `selectinload(entries)`; `:482-487` calls the same
    `balance_calculator.calculate_balances`; period-selection loop is at
    live `:463-466` (Phase-3 cited `:461-466` spans the `:461` comment plus
    the loop; behaviour exactly as described -- minor citation note, not a
    verdict change, already recorded in `04_source_of_truth.md:270-275`).
  - **PA-03 prior-status drift (surfaced, not smoothed -- R-8):** PA-03's
    specific `grid.balance_row` `scenario.id` None-deref 500 is
    **remediated** in live source -- `app/routes/grid.py:404` docstring
    names "F-099" and `:409` `if scenario is None:` / `return "", 204`
    guards it. The prior-audit `open` status (`00_priors.md:812`) is stale.
    PA-03 remains mapped `-> CRIT-01` in Part 8.B (the broader anchor-None
    display family persists); only its remediation status is the
    discrepancy. Recorded as reconciliation item R-8; not resolved here.
- **Phase-doc pointers:** `05_symptoms.md` Symptom #1 (`:18-389`) and
  Symptom #5 (`:1092-1561`); `03_consistency.md` F-001 (`:109-211`), F-002
  (`:214-280`), F-003 (`:283-...`), F-009 (`:649-724`), C2 rows #1/#5
  (`:6053,:6057`); `04_source_of_truth.md` Family A (`:35-294`), drift
  register rows #1/#5 (`:2138,:2142`); `06_dry_solid.md` D6-02/D6-03/D6-06/
  D6-08; `07_test_gaps.md` Part 7.A balance concepts + Part 7.B.
- **Open questions:** none open in this cluster. The subsumed Phase-3
  UNKNOWN members (F-004 Q-10; F-006/F-007/F-051/F-052/F-054-YG1 Q-15) and
  the Phase-4 UNCLEAR `current_anchor_period_id` (Q-20) carry their blocking
  Q for traceability, but all are ANSWERED (A-10/A-15/A-16/A-20) and locked
  by E-25 / E-19 (canonical-aggregate facet by E-18), consumed as this
  cluster's governing end state and NOT re-litigated (phase8_plan section 0;
  Part 8.B B.14). Phase 8's only carried-open tail (Q-26 sub-2) is LOW-05,
  not here.
- **Remediation direction:** One date-anchored, entries-aware balance /
  period-subtotal resolver owns "balance as of period for account" and
  guarantees the entry-aware reduction (entries loaded or computed inside
  it), while E-19 eliminates the NULL-anchor-period state, so grid,
  `/accounts`, `/savings`, dashboard, net worth, and the calendar all read
  the identical number from one producer (the E-04 invariant).
- **Blast radius / symptom link:** the current-pay-period checking balance
  ships wrong (lower) on `/savings`, `/accounts`, and the net-worth input
  relative to the grid and the dashboard balance card; the loan-principal
  facet of symptom #5 is cross-referenced to CRIT-02. Developer-reported
  symptoms **#1** and **#5**.

### CRIT-02 -- Stored loan principal/rate is never maintained on settle; no single event-derived loan resolver (symptoms #2/#3/#4 ONE family)

- **Severity + rubric justification:** CRITICAL. Refinement B (the
  developer-reported symptoms #2, #3, #4 -- ONE family, developer-directed),
  verified this session against cited displayed wrong dollars. On
  `/accounts/<id>/loan` the "Monthly P&I" card renders
  `summary.monthly_payment` (`loan/dashboard.html:129`) and the "Current
  Principal" card renders the STORED `params.current_principal`
  (`loan/dashboard.html:104`). Hand-recomputed this session from
  `05_symptoms.md`: **symptom #2** -- card **$1901.03** (site-7 `n=312`,
  `amortization_engine.py:950-954`) vs first projected schedule row
  **$1898.50** (site-3 `n=313`, `:486-493`), a $2.53 same-day same-page
  spread, and the card alone moves to $1903.57 a calendar month later
  (`05_symptoms.md:548-589`); **symptom #3** -- ARM stored
  **$300,000.00** unchanged through four settled PITI transfers
  (expected $299,611.64 after transfer 1's $388.36 principal portion)
  until a manual edit (`05_symptoms.md:791-817`); **symptom #4** -- 5/5
  ARM displayed Monthly P&I **$2,460.45** (month 24) -> **$2,463.28**
  (month 25), +$2.83 inside the fixed-rate window, both diverging from
  the correct constant **$2,398.20** (`05_symptoms.md:949-983`; F-026
  `03_consistency.md:1968-1994`). Continuous in shape with the developer's
  $1911.54 / $1914.34 / $1912.94 -> $1910.95; no error raised.
- **Category:** drift, source-of-truth, DRY.
- **Plain-language description:** There is no single routine that owns
  "this loan's current principal, monthly payment, and schedule." The
  stored `loan_params.current_principal` column has no settle-driven
  writer at all -- a grep this session found zero attribute writes to it
  anywhere in `app/` or `scripts/`, and none of the settle / status
  modules even imports the model -- so confirmed payments never reduce it;
  its only writer is a human typing into the dashboard form. For an ARM
  the engine reads that frozen column verbatim and re-amortizes it over a
  calendar-shrinking remaining-months count, so the displayed Monthly P&I
  creeps up a few dollars every month inside the supposedly fixed-rate
  window and the "Current Principal" card never moves as transfers settle.
  Meanwhile the schedule rows, the savings PITI, debt-strategy, refinance,
  and net worth each assemble their own (principal, rate, n) triple, so
  the same loan on the same day shows several different payments and
  several different principals. Symptoms #2, #3, and #4 are three faces of
  this one un-maintained column / no-single-resolver root.
- **Subsumes:**
  - Symptoms **#2/#3/#4** -- ONE finding family, developer-directed
    (`05_symptoms.md:1049-1088`, "#2/#3/#4 collapse onto the one un-maintained
    `current_principal` column"; phase8_plan section 4 P8-b).
  - Phase 3: **F-008** debt_total UNKNOWN (Q-15; internal stored-vs-engine
    DIVERGE holds regardless -- symptom #5 loan facet); **F-013**
    monthly_payment DIVERGE (SILENT+DEFINITION+PLAN; sym #2; Q-09/Q-17);
    **F-014** loan_principal_real DIVERGE (SOURCE+SCOPE+SILENT; sym #3;
    Q-15/Q-11); **F-015** loan_principal_stored DIVERGE (SOURCE; Q-11);
    **F-016** loan_principal_displayed UNKNOWN (Q-11); **F-026** 5/5 ARM
    stability DIVERGE (SILENT+PLAN; sym #4; Q-17).
  - Phase 4: `budget.loan_params.current_principal` **UNCLEAR (Q-22)**;
    `budget.loan_params.interest_rate` **UNCLEAR (Q-23)**;
    `budget.loan_params.original_principal` AUTHORITATIVE;
    `budget.rate_history.interest_rate` AUTHORITATIVE.
  - Phase 6: **D6-01** (E-18, no single loan resolver; every surface
    re-assembles its own `(principal, rate, n)`).
  - Phase 7: `loan_principal_real` NO-PINNED-TEST (sym #3); `monthly_payment`
    F-013/F-026 cross-site + ARM-window UNTESTED; `loan_principal_displayed`
    PRODUCER-UNKNOWN; `debt_total` dual-base conditional anti-coverage flag
    (Q-15; `07_test_gaps.md:3924`).
  - Prior-audit: **PA-28** (`calculate_remaining_months` zero test coverage --
    the symptom-#4 mechanism).
  - Resolved intent: Q-09/A-09 (E-18), Q-11/A-11 (E-18), Q-15/A-15 (E-18),
    Q-17/A-17 (E-18), Q-22/A-22 (E-18), Q-23/A-23 (E-18).
  - PT pointers: PT-07, PT-08, PT-06, PT-02 (deferred-not-authored).
- **Governing E-NN:** E-18 (one pure resolver derives balance,
  monthly_payment, schedule from the event stream; `current_principal` /
  `interest_rate` retired as authoritative scalars).
- **Evidence (re-resolved to live source this session, key line quoted):**
  - Zero settle-writer (re-run this session):
    `grep -rEn "\.current_principal\s*=[^=]" app/ scripts/ --include='*.py'`
    -> **zero matches** (exit 1). No attribute write to `current_principal`
    anywhere; the settle path cannot reduce it.
  - `app/routes/loan.py:668-674` -- sole post-creation writer:
    `_PARAM_FIELDS = { "current_principal", ... }` / `for field, value in
    data.items(): if field in _PARAM_FIELDS: setattr(params, field, value)`
    (the manual form; symptom #2's "$1910.95 after editing").
  - `app/services/amortization_engine.py:913` --
    `current_principal = Decimal(str(params.current_principal))` (STORED
    read); `:950-954` ARM scalar `if is_arm and remaining > 0:
    monthly_payment = calculate_monthly_payment(current_principal, rate,
    remaining,)`; `:977-978` `if is_arm:` / `cur_balance =
    current_principal` (STORED verbatim, ARM); `:486-493` schedule site-3
    `months_left = max_months - month_num + 1` / `calculate_monthly_payment(
    balance, current_annual_rate, months_left,)`.
  - `app/services/amortization_engine.py:136-142` --
    `calculate_remaining_months`: `if as_of is None: as_of = date.today()`
    ... `return max(0, term_months - months_elapsed)` (the `n` that shrinks
    by 1 every calendar month -- symptom #4 driver).
  - Inert fixed-window columns (grep this session):
    `arm_first_adjustment_months` / `arm_adjustment_interval_months` appear
    ONLY in `app/routes/loan.py`, `app/models/loan_params.py`,
    `app/schemas/validation.py` -- **zero** consumers in
    `amortization_engine.py` or any calculation module.
  - `app/templates/loan/dashboard.html:104` --
    `${{ "{:,.2f}".format(params.current_principal|float) }}` (the bold
    card renders STORED regardless of loan type); `loan.py:429` computes
    `proj` but `:553-557` passes `params=params` for the card (the engine
    value is not wired to it).
  - `app/routes/debt_strategy.py:172-173` -- `if params.is_arm:` /
    `return principal` (ARM returns the stored column verbatim).
  - `app/services/savings_dashboard_service.py:840` `principal =
    Decimal(str(lp.current_principal))` -> `:855` `total_debt += principal`
    (debt card STORED) while `:373` `current_bal = proj.current_balance`
    (engine) -- F-008 internal stored-vs-engine inconsistency in one
    service.
  - 16 live `calculate_monthly_payment(` call sites (grep this session,
    excluding the `def`) confirm the F-013 16-site multi-surface count.
- **Phase-doc pointers:** `05_symptoms.md` Symptom #2 (`:392-682`),
  Symptom #3 (`:685-879`), Symptom #4 (`:882-1047`), collapse note
  (`:1049-1088`); `03_consistency.md` F-008 (`:580-645`), F-013
  (`:1009-1147`), F-014 (`:1150-1250`), F-015 (`:1253-1298`), F-016
  (`:1301-1356`), F-026 (`:1936-2036`), C2 rows #2/#3/#4 (`:6054-6056`);
  `04_source_of_truth.md` Family B principal (`:296-488`), settle-update
  trace (`:489-655`), drift register #2/#3/#4 (`:2139-2141`);
  `06_dry_solid.md` D6-01.
- **Open questions:** none open in this cluster. The subsumed Phase-3
  members carried Q-09/Q-11/Q-15/Q-17 (F-013/F-014/F-016/F-026) and the
  Phase-4 UNCLEAR `current_principal` (Q-22) / `interest_rate` (Q-23);
  each blocking Q is cited for traceability, all are ANSWERED
  (A-09/A-11/A-15/A-17/A-22/A-23) and locked by E-18, consumed as the
  governing end state and NOT re-litigated (phase8_plan section 0;
  Part 8.B B.14).
- **Remediation direction:** One pure resolver derives the (balance,
  monthly_payment, schedule) triple on read by replaying confirmed
  payments forward from the latest user-verified anchor and honoring the
  ARM fixed-rate window, with `current_principal` / `interest_rate`
  retired as authoritative scalars and the "edit current principal"
  control reframed as an append-only dated anchor event, so every loan
  surface reads one value.
- **Blast radius / symptom link:** wrong Monthly P&I and Current Principal
  on `/accounts/<id>/loan` (the card, the schedule rows), and the same
  scalar drifts the savings PITI (`savings_dashboard_service.py:846`) and
  the recurring-transfer auto-amount (`loan.py:1225-1234`). Developer-
  reported symptoms **#2**, **#3**, **#4**; loan-base facet of symptom #5
  (cross-ref CRIT-01).

### CRIT-03 -- FICA Social-Security wage-base cap bypassed on the calibration path

- **Severity + rubric justification:** CRITICAL. C3-pre-listed candidate;
  severity re-derived this session from the rubric against a cited displayed
  wrong dollar, not copied from C3. The calibrated paycheck's FICA line and
  net pay are wrong: `apply_calibration` charges flat
  `gross * effective_ss_rate` every period with no wage-base or
  cumulative-wage input (`calibration_service.py:139-141`; grep this session
  found zero `ss_wage_base`/`cumulative_wages` in `calibration_service.py`),
  while the bracket path zeros SS once cumulative >= `ss_wage_base`
  (`tax_calculator.py:300-306`). Worked example re-read this session
  (`03_consistency.md:3072-3091`; C3 `:6069`): $312,000 salary, 26 periods,
  $12,000/period gross, calibration active -> calibrated year SS
  **$19,344.00** vs correct **$11,439.00** = **+$7,905.00/yr** FICA
  overstatement, net pay understated **$744.00 per over-cap period**
  (periods 17-26 charged $744.00 vs the correct $0.00; period 16 $744.00 vs
  $279.00), no error raised, reachable for exactly the documented
  pay-stub-calibration use case -- a wrong dollar on the salary/paycheck
  projection the developer relies on, the divergence not visible as an
  error.
- **Category:** drift, definition.
- **Plain-language description:** Social Security tax legally stops once a
  person's year-to-date wages reach the annual wage base. The bracket-based
  paycheck path enforces that cap. The calibration path -- the one used
  precisely when a user enters their real pay stub -- does not: its function
  has no parameter for cumulative wages and never references the wage base,
  so it keeps charging Social Security in every pay period all year. A high
  earner who calibrates from a pay stub therefore sees an overstated FICA
  line and an understated net pay for every pay period after the cap, with
  no error shown. For a $312k earner this is roughly $7,905 of phantom
  Social Security tax per year.
- **Subsumes:**
  - Phase 3: **F-037** fica DIVERGE (DEFINITION; "none -- IRS cap is a hard
    invariant (PA-21)").
  - Phase 7: `fica` COVERED for the bracket path, calibration-path SS-cap
    bypass NEVER exercised (`07_test_gaps.md:1590-1617`).
  - Prior-audit: **PA-21** (no test verifies SS stops accruing past
    `ss_wage_base`).
  - PT pointer: PT-17.
- **Governing E-NN:** NONE -> correctness invariant (the IRS SS wage base is
  a hard invariant, `02_concepts.md:1715-1719`; PA-21); cross-link E-20
  (calibration immutable snapshot).
- **Evidence (re-resolved to live source this session, key line quoted):**
  - `app/services/calibration_service.py:106` --
    `def apply_calibration(gross_biweekly, taxable_biweekly, calibration)`
    (no `cumulative_wages` parameter, no `ss_wage_base` reference); `:129`
    `ss_rate = Decimal(str(calibration.effective_ss_rate))`; `:139-141`
    `"ss": (gross * ss_rate).quantize(TWO_PLACES,
    rounding=ROUND_HALF_UP)` -- flat, uncapped, every period.
  - `grep -n "ss_wage_base\|cumulative_wages" app/services/calibration_service.py`
    -> **zero matches** (exit 1) this session: the cap input is structurally
    absent from the calibration path.
  - `app/services/tax_calculator.py:294,300-306` --
    `ss_wage_base = Decimal(str(fica_config.ss_wage_base))`;
    `if cumulative >= ss_wage_base: ss_tax = ZERO` /
    `elif cumulative + gross > ss_wage_base: ss_taxable = ss_wage_base -
    cumulative; ...` (the bracket path enforces the cap).
  - `app/services/paycheck_calculator.py:160-173` -- `use_calibration = (
    calibration is not None and getattr(calibration, "is_active", False))`
    then `ss_biweekly = cal_taxes["ss"]` -- the gate that routes a
    calibrated high earner onto the uncapped figure.
- **Phase-doc pointers:** `03_consistency.md` F-037 (`:3020-3112`), C3
  (`:6069`); `00_priors.md` PA-21 (`:830`); `02_concepts.md:1715-1719,
  1730-1735` (the FICA intended definition / hard invariant);
  `07_test_gaps.md` `fica` calibration-path verdict (`:1590-1617`).
- **Open questions:** none. The IRS SS wage-base cap is a hard invariant
  (`02_concepts.md:1715-1719`), not a "what is intended" ambiguity; F-037
  records "Open questions for the developer: none new." PA-21 is the
  prior-audit test gap this finding confirms and extends, not a blocking
  developer question.
- **Remediation direction:** Thread cumulative YTD wages and `ss_wage_base`
  into the calibration path (or zero the calibrated SS once YTD >=
  `ss_wage_base`) so it honors the same hard SS-cap invariant as
  `tax_calculator.calculate_fica`.
- **Blast radius / symptom link:** the FICA line and net pay on every
  calibrated paycheck / salary projection for any earner whose annual wages
  exceed `ss_wage_base`; no developer-reported symptom (latent -- the PA-21
  confirmation-and-extension).

### CRIT-04 -- Retirement phantom income (explicit-zero SWR treated as unset) and zero-return account dropped from the weighted return

- **Severity + rubric justification:** CRITICAL. C3-pre-listed candidate;
  severity re-derived this session from the rubric against cited displayed
  wrong figures, not copied from C3. On the retirement dashboard the SWR
  slider displays `current_swr` resolved with `is None` semantics
  (`retirement_dashboard_service.py:304-309`) while the gap/income
  projection resolves the SWR with a truthiness `or "0.04"`
  (`:217-221`, `:220`) and the chart income is `(projected_total_savings *
  swr / 12)` (`:240`). Worked example re-read this session
  (`03_consistency.md:3551-3573`; C3 `:6070`): an explicitly-set
  `safe_withdrawal_rate = Decimal("0.0000")` -> slider shows **0.00%** but
  the projection uses `swr = 0.04`, so with `projected_total_savings =
  $1,200,000` the chart shows **$4,000.00/mo** phantom retirement income the
  slider says is zero. Separately, `:321` `if params and
  params.assumed_annual_return:` truthiness drops a zero-return $100,000
  account from the weighted-return denominator: two equal $100k accounts at
  0.00% and 7.00% have a true blended return of **3.50%** but the slider
  displays **7.00%**. Both render with no error -- wrong dollars and a wrong
  rate on the retirement-planning page the developer relies on.
- **Category:** drift, source-of-truth, DRY.
- **Plain-language description:** On the retirement dashboard the
  safe-withdrawal-rate slider and the projected-income math read the same
  stored value two different ways. The slider correctly treats an
  explicitly-entered 0% withdrawal rate as zero; the gap/income projection
  treats 0% as "unset" and silently substitutes 4%. A user who deliberately
  set a 0% withdrawal rate therefore sees a 0.00% slider but a projected
  retirement income computed at 4% -- roughly $4,000/month of income the
  slider says is zero. Separately, the balance-weighted average return drops
  any account whose assumed return is exactly 0% (a stable-value/cash
  sleeve) because the code uses a truthiness check instead of an explicit
  "is it set" check, overstating the displayed blended return -- 7.00% shown
  for a portfolio whose true blended return is 3.50%. Both are the
  coding-standards "a zero is not a missing value" rule violated on money.
- **Subsumes:**
  - Phase 3: **F-042** growth DIVERGE (SILENT; "none -- PA-04/PA-05
    reconciliation").
  - Phase 4: `budget.investment_params.assumed_annual_return` AUTHORITATIVE
    (0-vs-None read hazard, Q-24 #2 / F-042 facet).
  - Phase 6: **D6-10** (E-? -> PA-05; the 4% SWR / 7% assumed-return fallback
    magic literal duplicated across two unit conventions).
  - Phase 7: `growth` COVERED for the engine, LOOSE-ONLY for the slider/gap
    reconciliation.
  - Prior-audit: **PA-04** (`compute_slider_defaults` float SWR; zero SWR
    treated as unset; zero-return accounts excluded from the weighted
    average); **PA-05** (hardcoded `0.04`/`4.0`/`7.0` magic fallbacks).
  - PT pointer: PT-18.
- **Governing E-NN:** E-12 (a zero monetary value is not a missing value;
  no truthiness on money) + PA-04 / PA-05; D6-10 folded.
- **Evidence (re-resolved to live source this session, key line quoted):**
  - `app/services/retirement_dashboard_service.py:217-221` -- `swr = (
    swr_override if swr_override is not None else
    Decimal(str(settings.safe_withdrawal_rate or "0.04")) if settings else
    Decimal("0.04"))` -- the truthiness `or "0.04"` at `:220` (an
    explicit-zero SWR is silently replaced by 0.04); `:240`
    `(gap_result.projected_total_savings * swr / 12).quantize(
    Decimal("0.01"))` -- the gap math / chart income uses that `swr`.
  - `app/services/retirement_dashboard_service.py:304-309` -- `if settings
    is None or settings.safe_withdrawal_rate is None: current_swr =
    _DEFAULT_SWR_PCT` / `else: current_swr =
    (settings.safe_withdrawal_rate * _PCT_SCALE).quantize(_PCT_QUANTUM,)`
    -- the displayed slider uses `is None`, so an explicit zero shows
    0.00% while `:220` uses 0.04 for the same render.
  - `app/services/retirement_dashboard_service.py:321` -- `if params and
    params.assumed_annual_return:` (truthiness) then `:323-324`
    `total_balance += bal` / `weighted_return += bal *
    params.assumed_annual_return`; `:326-328` `current_return =
    (weighted_return / total_balance * _PCT_SCALE).quantize(_PCT_QUANTUM)`
    -- a zero-return account is skipped entirely, shrinking the denominator.
- **Phase-doc pointers:** `03_consistency.md` F-042 (`:3456-3597`), C3
  (`:6070`); `00_priors.md` PA-04 (`:813`), PA-05 (`:814`), E-12 (`:362`);
  `06_dry_solid.md` D6-10; `07_test_gaps.md` `growth` slider/gap verdict.
- **Open questions:** none open in this cluster. PA-04 / PA-05 are resolved
  prior-audit findings; the truthiness / zero-handling defects are findings
  by the coding-standards "0 vs None" rule (F-042 "Open questions for the
  developer: none new"). The secondary `retirement_dashboard_service.py:224`
  `estimated_retirement_tax_rate` truthiness guard's model-comment
  NULL-semantics contract is the Q-26 sub-2 carried tail, recorded at
  LOW-05 and carried to Phase 9 unchanged -- it is NOT part of this
  cluster's verdict and is not resolved here.
- **Remediation direction:** Make `compute_gap_data` resolve the SWR with
  the same `is None` semantics (and a named fractional constant) as
  `compute_slider_defaults`, and replace the `:321` `assumed_annual_return`
  truthiness with `is not None` so an explicit-zero rate keeps its balance
  weight, so the displayed slider and the value driving the projection are
  one number.
- **Blast radius / symptom link:** the projected retirement income, the
  income gap, and the SWR / return sliders on the retirement dashboard
  render wrong for an explicit-zero SWR or any zero-return account holding
  material balance; no developer-reported symptom (latent -- the PA-04 /
  PA-05 confirmation).

### CRIT-05 -- Irreversible silent hard-delete of RECEIVED settled-income history

- **Severity + rubric justification:** CRITICAL by rubric Refinement A
  (data-loss): irreversible silent destruction of settled financial history
  on a normal user action; the harm is the loss, not a wrong displayed
  number. The irreversible-destruction path, re-resolved this session: a
  recurring income template whose paychecks were marked RECEIVED
  (`transactions.py:534-535`) -- RECEIVED is `is_settled=True,
  is_immutable=True` (`ref_seeds.py:81`) -- passes
  `template_has_paid_history`, which queries only
  `status_id.in_([DONE, SETTLED])` and omits RECEIVED
  (`archive_helpers.py:29-38`), so the guard at `templates.py:581` passes
  and the unconditional bulk delete at `templates.py:615-618` physically
  destroys the RECEIVED settled-income transactions while
  `templates.py:636` flashes "permanently deleted." There is no
  soft-delete and no recovery; this is the data-loss CRITICAL the rubric
  Refinement A defines (CLAUDE.md: real money mismanaged).
- **Category:** source-of-truth, definition.
- **Plain-language description:** When a user permanently deletes a
  recurring income template, the app first checks whether any linked
  transactions were "paid." That check only looks for two specific settled
  statuses and misses RECEIVED -- the status every income paycheck is given
  when it is marked done. So an income template whose paychecks were all
  marked received passes the "no payment history" check, and the code then
  unconditionally hard-deletes every linked transaction, physically
  destroying real received-income history while telling the user it was
  "permanently deleted." There is no soft-delete and no undo; the financial
  history is gone. It is a normal user action (deleting a recurring item),
  raises no error, and is irreversible.
- **Subsumes:**
  - Phase 3 cmp-3: **W-262** HOLDS literal **+ escalated RECEIVED
    hard-delete data-loss, axis UNKNOWN-Q-19** (`03_consistency.md:6043`);
    C3 item (`:6071`).
  - Phase 7: no controlled-vocabulary concept (data-loss is not a displayed
    number); correctly absent from the Part 7.A census.
  - Resolved intent: Q-19/A-19 (E-22; "PROVEN silent irreversible data-loss
    path confirmed").
- **Governing E-NN:** E-22 (hard-delete guards use the semantic `is_settled`
  boolean; the destructive delete is itself constrained to non-settled rows).
- **Evidence (re-resolved to live source this session, key line quoted):**
  - `app/routes/transactions.py:534-535` -- `if txn.is_income:` /
    `status_id = ref_cache.status_id(StatusEnum.RECEIVED)` (mark_done
    assigns RECEIVED to all income; `:610` `txn.status_id = status_id`).
  - `app/ref_seeds.py:81` -- `{"name": "Received", "is_settled": True,
    "is_immutable": True, "excludes_from_balance": False}` (RECEIVED is a
    settled, immutable status).
  - `app/utils/archive_helpers.py:29-38` -- `paid_id =
    ref_cache.status_id(StatusEnum.DONE)` / `settled_id =
    ref_cache.status_id(StatusEnum.SETTLED)` / `Transaction.status_id.in_(
    [paid_id, settled_id])` -- the predicate enumerates only `[DONE,
    SETTLED]`; RECEIVED is not in the list, so it returns False for a
    RECEIVED-only income template (`transfer_template_has_paid_history`
    `:53-60` has the identical enumeration).
  - `app/routes/templates.py:581` -- `if
    archive_helpers.template_has_paid_history(template.id):` (the only
    guard); `:612-614` comment "Only Projected transactions should remain
    (history check passed), but delete unconditionally for safety.";
    `:615-618` `db.session.query(Transaction).filter(Transaction.template_id
    == template.id,).delete(synchronize_session="fetch")` (unconditional
    physical delete); `:636` flash "permanently deleted".
- **Phase-doc pointers:** `00_priors.md` E-22 (`:234-244`);
  `03_consistency.md` cmp-3 W-262 (`:6043`), C3 (`:6071`);
  `09_open_questions.md` Q-19 / A-19.
- **Open questions:** none. Q-19 is ANSWERED by A-19 / E-22 ("PROVEN silent
  irreversible data-loss path confirmed"); no developer adjudication is
  pending for this cluster (E-22 also confirms the sibling
  `account_has_history` / `category_has_usage` predicates have no analogous
  gap, read-verified in the E-22 source line).
- **Remediation direction:** Replace the enumerated `[DONE, SETTLED]`
  predicate in both `template_has_paid_history` and
  `transfer_template_has_paid_history` with the semantic `Status.is_settled`
  boolean and additionally constrain the `hard_delete_template` bulk delete
  to non-settled rows, so settled financial history cannot be physically
  destroyed even if the guard predicate later regresses.
- **Blast radius / symptom link:** irreversible loss of RECEIVED
  settled-income transaction history on a normal "permanently delete
  recurring income template" action; no developer-reported symptom (latent
  data-loss path -- the harm is destruction of settled history, not a wrong
  displayed number).

## HIGH tier (provisional)

### HIGH-01 -- Cross-page balance-equality regression meta-gap: the two worst symptoms have no falsifying lock

- **Severity + rubric justification:** HIGH. Rubric: "the absence of any
  regression lock for a proven CRITICAL" (the Phase-7 cross-page meta-gap).
  The two worst developer-reported symptoms (#1 $160 grid vs $114.29
  `/savings`; #5 `/accounts` matches nowhere), both CRITICAL under CRIT-01,
  have zero falsifying test: re-run this session, the three audit-plan-
  mandated cross-page-equality greps over live `tests/` return 0 matches
  (`grep -rlE 'auth_client\.get.*grid.*auth_client\.get.*accounts'
  tests/test_routes/` -> grep exit 1; the grid-vs-savings form -> exit 1;
  `grep -rlE 'grid.*==.*savings|checking.*==.*accounts' tests/` -> exit 1).
  This finding has no displayed wrong dollar of its own (it is a test-gap,
  not a producer), so HIGH not CRITICAL by the rubric; its severity is the
  inability to catch CRIT-01's regression, not a second wrong number.
- **Category:** test gap.
- **Plain-language description:** The single test the developer most needs
  to catch the checking-balance bug does not exist. CRIT-01 proves the same
  account/period/scenario shows $160 on the grid and $114.29 on `/savings`;
  the only test that would fail when that happens is one that loads two of
  {grid, `/savings`, `/accounts`, dashboard, net worth} in one test and
  asserts they show the same balance. No such test exists anywhere in the
  suite (confirmed this session by re-running the three audit-plan greps --
  all zero matches), and the one near-miss computes its own balance and
  never calls the grid route. So the developer's two most concrete reported
  wrong-dollar bugs (#1 and #5) have no regression anchor: a fix could
  regress silently and every test would stay green.
- **Subsumes:**
  - Phase 7: the explicit cross-page balance-equality meta-gap
    (`07_test_gaps.md:3231-3257`; three audit-plan-mandated greps, 0 matches).
  - Prior-audit: **PA-10** (no penny-level 52+-period balance test);
    **PA-11** (no balance-calculator idempotency test).
  - PT pointer: PT-01 (the cross-page fixture; subsumes the symptom #1/#5
    checking facets).
- **Governing E-NN:** NONE -> test-gap (the E-04 invariant of the whole
  balance family is untested).
- **Evidence (re-resolved to live source this session, key line quoted):**
  - The three audit-plan-mandated cross-page-equality greps re-run over
    live `tests/` this session: `grep -rlE
    'auth_client\.get.*grid.*auth_client\.get.*accounts' tests/test_routes/`
    -> grep exit 1 (zero matches); the `...grid...savings...` form -> exit
    1; `grep -rlE 'grid.*==.*savings|checking.*==.*accounts' tests/` ->
    exit 1. No test renders two balance surfaces in one function and
    asserts equality.
  - The single near-miss `tests/test_routes/test_accounts.py:2211`
    `def test_checking_detail_matches_grid_balance(` is live but (per
    Phase 7 `07_test_gaps.md:38-53`, re-confirmed) computes its own
    entries-absent `calculate_balances`, never calls `/grid`, uses no
    Projected envelope expense with cleared entries -- it passes against
    the divergent code.
  - **PA-10 / PA-11 prior-status drift (surfaced, not smoothed -- R-9,
    new this session):** PA-10 (`00_priors.md:819`, status `open`) states
    "No test verifies penny-level accuracy across 52+ periods"; live
    source has `tests/test_services/test_balance_calculator.py:532`
    `def test_52_period_penny_accuracy(` with `periods = [FakePeriod(i)
    for i in range(52)]` (`:544`). PA-11 (`00_priors.md:820`, status
    `open`) states "No test calls `calculate_balances` twice on identical
    inputs and asserts identical outputs"; live source has
    `test_balance_calculator.py:907`
    `def test_idempotent_same_inputs_same_outputs(` ("Calls the function
    twice with identical inputs ... Repeated calls produce exactly the
    same Decimal results"). Both PA-10/PA-11 `open` premises are stale
    w.r.t. live source. These tests pin the **single-producer**
    `calculate_balances` in isolation (FakePeriod/FakeTxn), NOT the
    cross-page equality HIGH-01 governs -- so HIGH-01's substance (no
    cross-page lock; the three greps return 0 matches this session)
    stands, and PA-10/PA-11 remain mapped `-> HIGH-01` in Part 8.B (the
    surjection is unchanged). Only PA-10/PA-11's `open`/"no test" status
    is the discrepancy; recorded as reconciliation item R-9, NOT resolved
    here (P8-e owns the mechanical reconciliation against `00_priors.md`).
- **Phase-doc pointers:** `07_test_gaps.md` Part 7.B cross-page meta-gap
  (`:3231-3257`), Part 7.A balance slice (`:17-126`), 7.F.1 row 17
  (`:3850`); `00_priors.md` PA-10/PA-11 (`:819-820`); CRIT-01 (the proven
  CRITICAL this gap fails to lock).
- **Open questions:** none blocking. PA-10/PA-11 are prior-audit test-gap
  items, not developer-decision questions; their stale `open` status vs
  live source is reconciliation item R-9 (a documentation-correction
  against `00_priors.md`, the same class as R-8 for PA-03), surfaced not
  resolved here.
- **Remediation direction:** Add one cross-page fixture (PT-01) that
  renders the grid, `/savings`, `/accounts`, dashboard, and net-worth
  balance for one account/period/scenario with a Projected envelope
  expense carrying cleared entries and asserts all surfaces return the
  identical Decimal (the E-04 invariant), with a negative control proving
  it is not always-red.
- **Blast radius / symptom link:** no displayed wrong dollar of its own;
  it is the missing regression anchor for CRIT-01's symptoms **#1** and
  **#5** -- the developer's two worst reported bugs can regress with the
  suite green. Latent (test-gap meta-finding).

### HIGH-02 -- Calendar month-end "End Balance" is a second, non-entries-aware path with a period-selection off-by-one and an all-status filter

- **Severity + rubric justification:** HIGH. Rubric: "stored/computed drift
  not yet observed wrong but sufficient to produce a wrong dollar under
  realistic inputs." The calendar "End Balance" is a second, non-entries-
  aware balance path: for an account/month with a Projected envelope expense
  carrying cleared entries it renders a *different* dollar than the grid for
  the same month, by the identical F-003/F-009 mechanism (CRIT-01's root,
  here on `calendar_service.py`). No displayed wrong dollar is hand-derived
  in a Phase-3/5 worked example specific to the calendar surface (the W-277
  axis is UNKNOWN-Q-18 on period-selection; the entries-load axis inherits
  CRIT-01's $45.71-class gap), so by contract item 2 it is HIGH not CRITICAL
  absent a calendar-specific cited wrong dollar. **R-3 (surfaced, not
  smoothed):** C3 (`03_consistency.md:6072`) pre-listed the W-065/W-277
  calendar drift as CRITICAL ("a different dollar amount than the grid");
  phase8_plan P8-b scopes five CRITICAL clusters and routes the calendar
  here; P8-e re-derives the final tier from the rubric against the cited
  displayed wrong dollar (C3 is an input to re-test, not an authority to
  copy).
- **Category:** drift, source-of-truth, DRY.
- **Plain-language description:** The monthly calendar's "End Balance"
  computes the balance a second, separate way from the grid/savings/
  dashboard balance path. It (a) picks the last pay period that *ends on or
  before* the calendar month-end, which can be up to ~13 days stale -- it is
  not a true balance-as-of-the-month-end; and (b) builds its transaction
  query without eager-loading the per-purchase entries, so -- exactly like
  the CRIT-01 mechanism -- it holds back the full estimated envelope amount
  instead of only the not-yet-cleared part. For a checking account/month
  with a Projected envelope expense whose purchases already cleared the
  bank, the calendar therefore shows a lower (wrong) "End Balance" than the
  grid for the same month, with no error and no label. There is also an
  all-status vs grid-Projected-only per-day filter difference (W-065). When
  the account's anchor period is unset the calendar returns a hard $0.00 (a
  fifth distinct anchor-None behavior).
- **Subsumes:**
  - Phase 3 cmp-3: **W-277** UNKNOWN-Q-18 + escalated entries-load
    SILENT_DRIFT (inherits F-003/F-009) + SCOPE Q-16 (`03_consistency.md:6042`);
    **W-065** HOLDS + escalated filter-set DEFINITION_DRIFT (calendar
    all-status vs grid Projected-only; F-004 cross-ref) (`:6044`); C3 item
    (`:6072`).
  - Phase 6: **D6-04** (E-27, no single "balance as of date" path).
  - F-003 / F-009 calendar instance (primary home CRIT-01; the
    `calendar_service` `selectinload` omission is the same defect surface).
  - Resolved intent: Q-18/A-18 (E-27).
  - PT pointer: PT-01 (calendar flavor).
- **Governing E-NN:** E-27 (one canonical entries-aware "balance as of date"
  path evaluated at the calendar month-end date).
- **Evidence (re-resolved to live source this session, key line quoted):**
  - `app/services/calendar_service.py:435` --
    `def _compute_month_end_balance(account, year, month, user_id,
    scenario,)`.
  - `:449-450` -- `if account.current_anchor_period_id is None:` /
    `return Decimal("0")` (the anchor-None hard-zero; D6-02 fifth
    behavior).
  - `:461-465` -- `# Find the last period whose end_date <= last_day of
    month.` / `for p in all_periods:` / `if p.end_date <= last_day:` /
    `target_period = p` (the days-stale period-end, not month-end;
    W-277/Q-18 off-by-one).
  - `:471-480` -- the transaction query filters `Transaction.account_id`,
    `Transaction.scenario_id`, `Transaction.pay_period_id.in_(period_ids)`,
    `Transaction.is_deleted.is_(False)` and has **NO
    `.options(selectinload(Transaction.entries))`** (the entries-load
    omission; `balance_calculator.py:353-354` then short-circuits to
    `effective_amount` -- the F-003/F-009 SILENT_DRIFT, here on a second
    path).
  - `:482-487` -- `balances, _ = balance_calculator.calculate_balances(
    account.current_anchor_balance, account.current_anchor_period_id,
    all_periods, all_txns,)` then `:489 return
    balances.get(target_period.id, Decimal("0"))`.
- **Phase-doc pointers:** `06_dry_solid.md` D6-04 (`:299-371`);
  `03_consistency.md` cmp-3 W-277/W-065 (`:6042-6044`), C3 (`:6072`), F-003
  (`:283-339`), F-009 (`:649-725`); `00_priors.md` E-27 (`:298-308`);
  `07_test_gaps.md` Part 7.B D6-04 row (`:3268`); `09_open_questions.md`
  Q-18 / A-18.
- **Open questions:** none blocking remediation. Q-18 (the period-selection
  / per-transaction effective-date semantics) is ANSWERED by A-18 / E-27
  (one canonical entries-aware balance-as-of-date, the effective-date rule
  flagged as E-27's own open implementation detail), consumed as this
  cluster's governing end state and NOT re-litigated; the cited W-277
  "UNKNOWN-Q-18" axis carries Q-18 for traceability only. The severity-tier
  reconciliation R-3 (C3 CRITICAL vs phase8_plan HIGH) is surfaced for
  P8-e, not resolved here.
- **Remediation direction:** Route the calendar month-end through the
  single canonical entries-aware "balance as of date D" producer (E-27),
  anchor-resolved per E-19, evaluated at the true calendar month-end date
  sharing `_entry_aware_amount` and the E-25 subtotal base, removing the
  `:449-450` zero fallback and the bespoke period-selection slice.
- **Blast radius / symptom link:** the calendar's "End Balance" ships
  wrong (lower, and days-stale) versus the grid for any account/month with
  a Projected envelope expense carrying cleared entries; no developer-
  reported symptom (latent -- the same CRIT-01 mechanism on a second
  surface; the developer's #1 is the grid-vs-`/savings` face of this root).

### HIGH-03 -- Calibration `effective_*_rate` columns: snapshot-vs-derived staleness

- **Severity + rubric justification:** HIGH. Rubric: "the Phase-4 UNCLEAR
  stored columns (drift surface, blocked on a developer decision)." The four
  `salary.calibration_overrides.effective_*_rate` columns are Phase-4
  **UNCLEAR (Q-25)** -- frozen client-submitted snapshot vs live-derived,
  never re-derived at confirm or on profile edit. Two confirmed silent drift
  surfaces produce wrong federal/state/FICA withholding on every projected
  calibrated paycheck under realistic inputs (a tampered/stale two-step POST
  stores a rate pair inconsistent with the actual_* pair; or a post-
  calibration profile pre-tax-deduction/salary edit silently leaves the
  stored rate derived against the old taxable base). No Phase-3/5 hand-
  derived displayed wrong dollar specific to this column quartet, and the
  verdict is blocked on Q-25 -- so HIGH per the rubric's UNCLEAR-stored-
  column clause, not CRITICAL, and recorded WITH the blocking Q, NOT
  resolved (contract item 5).
- **Category:** source-of-truth, drift.
- **Plain-language description:** When a user calibrates from a real pay
  stub, the app derives four effective tax rates (federal, state, SS,
  Medicare) from that stub and stores them. Two problems: (1) the confirm
  step does not re-derive the rates server-side -- it stores whatever rate
  values the browser posted back as hidden form fields, independently from
  the actual_* dollar values it also stores, with no cross-check that
  rate == actual / base, so a stale or tampered two-step submission persists
  a rate pair inconsistent with the actual_* pair and nothing detects it;
  (2) the federal/state divisor depends on the profile's pre-tax deductions
  at preview time, and editing those deductions or salary afterward never
  recomputes the stored rate, so the saved calibration silently keeps a rate
  derived against the old taxable base. Either way, every later projected
  paycheck multiplies the stale/inconsistent stored rate against the live
  taxable/gross and shows wrong withholding and net pay, with no error.
  Whether these columns are meant to be a frozen pay-stub snapshot or a
  live-derived rate is a developer decision (Q-25); the ambiguity is itself
  the finding.
- **Subsumes:**
  - Phase 4: `salary.calibration_overrides.effective_federal_rate`
    **UNCLEAR (Q-25)**; `effective_state_rate` **UNCLEAR (Q-25)**;
    `effective_ss_rate` **UNCLEAR (Q-25)**; `effective_medicare_rate`
    **UNCLEAR (Q-25)** (the four share Q-25).
  - Phase 7: `taxable_income` Q-13 calibrate_preview sub
    BLOCKED-ON-OPEN-QUESTION (Q-13); `pre_tax_deduction` Q-13 pct-base sub
    BLOCKED-ON-OPEN-QUESTION (Q-13) (completes the `Subsumes` block to the
    exact reverse of Part 8.B B.9, which routes both Q-13 subs `-> HIGH-03`;
    a P8-a skeleton undercount carried as the corrected membership, the B.8
    carry-undercount precedent -- not a new finding).
  - Resolved intent: Q-25/A-25 (E-20 / E-28); Q-13/A-13 (E-20, the
    taxable-base contamination resolved as the concrete form of E-20).
- **Governing E-NN:** E-20 (calibration is an immutable, fully
  pay-stub-grounded snapshot) + E-28 (one DB-enforced domain).
- **Evidence (re-resolved to live source this session, key line quoted):**
  - `app/services/calibration_service.py:34` --
    `def derive_effective_rates(actual_gross_pay, ...)`; `:83`
    `effective_federal = (federal / taxable).quantize(`; `:91`
    `effective_ss = (ss / gross).quantize(` (the four are a
    denormalization of the row's own `actual_*` plus the profile
    pre-tax-deduction-derived `taxable`).
  - `app/routes/salary.py:1067` `def calibrate_preview(profile_id):`
    calls `:1105` `rates = derive_effective_rates(`; `:1130`
    `def calibrate_confirm(profile_id):` stores `:1161-1164`
    `effective_federal_rate=data["effective_federal_rate"],
    effective_state_rate=data["effective_state_rate"],
    effective_ss_rate=data["effective_ss_rate"],
    effective_medicare_rate=data["effective_medicare_rate"]` straight
    from the posted form -- grep this session confirms
    `derive_effective_rates` is referenced only at `salary.py:67`
    (import) and `:1105` (preview), **never inside `calibrate_confirm`**
    (no server-side re-derivation / cross-check on confirm).
  - `app/schemas/validation.py:1827` `class CalibrationConfirmSchema(
    BaseSchema):` / `:1858-1860` `effective_federal_rate = fields.Decimal(
    ... validate=validate.Range(min=0, max=1))` -- confirm validation only
    range-pins each rate to [0,1]; it does not enforce
    `effective_x == actual_x / base`.
  - `app/models/calibration_override.py:53-66` -- CHECK
    `effective_federal_rate >= 0 AND effective_federal_rate <= 1` (and
    state/ss/medicare); `:89-90` `effective_federal_rate =
    db.Column(db.Numeric(12, 10), nullable=False)` (NOT NULL, no
    derivation-freshness column).
  - `app/services/calibration_service.py:106`
    `def apply_calibration(gross_biweekly, taxable_biweekly,
    calibration):` / `:127` `federal_rate = Decimal(str(
    calibration.effective_federal_rate))` / `:139` `"ss": (gross *
    ss_rate).quantize(` -- the read path multiplies the STORED rate
    against the live per-period taxable/gross every calibrated paycheck
    (gated `calibration.is_active`, `paycheck_calculator.py:160-167`).
- **Phase-doc pointers:** `04_source_of_truth.md` Escalation 3
  (`:1735-1797`), drift register rate-source rows (`:2139-2141`);
  `03_consistency.md` F-038 (Q-13 calibration base; NOT-A-FINDING:AGREE in
  B.1); `07_test_gaps.md` `taxable_income`/`pre_tax_deduction` Q-13
  calibrate_preview subs (BLOCKED-ON-OPEN-QUESTION, B.9); `00_priors.md`
  E-20 / E-28; `09_open_questions.md` Q-25/A-25, Q-13/A-13.
- **Open questions:** **Q-25 (UNCLEAR, NOT resolved here -- contract item
  5).** The four `effective_*_rate` columns are Phase-4 UNCLEAR: under
  "frozen pay-stub snapshot" intent the actual_*-vs-rate inconsistency
  window is the defect; under "live derived rate" intent the missing
  recompute-on-profile-edit is the defect; the auditor does not pick a
  side. Q-25/A-25 records the governing end state (E-20 immutable fully
  pay-stub-grounded snapshot / E-28 one DB-enforced domain) consumed as
  this cluster's end state and NOT re-litigated; Q-13/A-13 (the taxable-
  base contamination) likewise resolved by E-20 and consumed, not
  re-opened. The blocking developer decision behind Q-25 (the column's
  intended semantics) is carried forward, recorded not resolved.
- **Remediation direction:** Per E-20, make calibration an immutable
  fully pay-stub-grounded snapshot -- `calibrate_confirm` re-derives the
  four rates server-side from the stored `actual_*` + taxable base (never
  trusting posted rate fields) and the stored rate pair is server-
  validated consistent with the stored actual_* pair -- and per E-28 keep
  the one DB-enforced [0,1] domain, so a stale or tampered two-step POST
  cannot persist an inconsistent rate.
- **Blast radius / symptom link:** wrong federal/state/FICA withholding
  and net pay on every projected paycheck for any user whose stored
  calibration rate pair is inconsistent with its actual_* pair
  (tampered/stale two-step POST) or whose profile pre-tax deductions /
  salary changed after calibrating; no developer-reported symptom (latent;
  Phase-4 UNCLEAR drift surface blocked on Q-25).

### HIGH-04 -- No centralized money-rounding helper: 24 banker's-rounding sites + 19-file `TWO_PLACES` redeclaration

- **Severity + rubric justification:** HIGH. Rubric: "stored/computed drift
  ... sufficient to produce a wrong dollar under realistic inputs";
  phase8_plan P8-c "the E-26 rounding-helper absence." Confirmed at live
  source this session: there is **no `app/utils/money.py` and no
  `def round_money` anywhere in `app/`** (`ls app/utils/` has no
  money/round module; `grep -rn "def round_money" app/` returns nothing) --
  E-26's single boundary helper does not exist. 24 monetary `.quantize()`
  boundary sites carry no `rounding=` argument and so default to Python's
  `ROUND_HALF_EVEN` (banker's), producing different cents than the
  `ROUND_HALF_UP` convention every hand-computed test assertion assumes --
  a wrong displayed cent under realistic inputs. HIGH (a cent, not a
  dollars-wide error; no single developer-reported symptom).
- **Category:** drift, DRY.
- **Plain-language description:** There is no single money-rounding helper.
  The two-decimal rounding rule is hand-redeclared as
  `TWO_PLACES = Decimal("0.01")` in 19 separate files, and 24 of the places
  that round a money value to cents call `.quantize(Decimal("0.01"))` with
  no rounding mode, so Python silently uses banker's rounding (round-half-
  to-even) instead of the round-half-up convention the rest of the codebase
  and every hand-computed test assertion assume. The same conceptual
  operation therefore produces round-half-up cents in ~99 places and
  banker's cents in 24, so a displayed figure can be off by a cent at a
  half-cent boundary -- concretely the investment/retirement projection
  chart balances, the savings `total_debt`/`total_monthly_payments`, and
  the retirement monthly investment-income figure. The centralized helper
  E-26 specifies does not exist anywhere in the app.
- **Subsumes:**
  - Phase 6: **D6-07** (E-26; `TWO_PLACES` in 19 files; 24 monetary
    `.quantize()` sites silently `ROUND_HALF_EVEN`).
  - Resolved intent: A-01 formalized as E-26 (Q-01/A-01).
  - PT pointer: PT-20c (D6-07 `round_money` golden-cents).
- **Governing E-NN:** E-26 (full-precision intermediates; one centralized
  `round_money` boundary helper with named sanctioned variants).
- **Evidence (re-resolved to live source this session, key line quoted):**
  - Helper absence (re-run this session): `ls app/utils/` has no
    money/round module; `grep -rn "def round_money" app/` -> zero
    matches. E-26's single `app/utils/money.py` `round_money` boundary
    helper does not exist anywhere in `app/`.
  - `app/routes/loan.py:966-968` -- `committed_interest_saved = (
    original_interest - committed_interest).quantize(Decimal("0.01"))`
    -- **no `rounding=` -> ROUND_HALF_EVEN** (D6-07 register-(b) row 24;
    this is also the F-021 ROUNDING site cross-referenced from HIGH-08).
  - D6-07 register (a) the 19 declaring files and (b) the 24
    bare-quantize sites are carried as Phase 6's exact, source-confirmed
    counts (`06_dry_solid.md:619-696`); E-26's read-verified seed sites
    `investment_projection.py:93,96,159` and
    `retirement_dashboard_service.py:197,211,214` are rows 1-3 / 19-21 of
    register (b). The sanctioned `savings_goal_service.py:462-463`
    `ROUND_CEILING` exception is explicitly NOT a finding (E-26
    `00_priors.md:290`).
- **Phase-doc pointers:** `06_dry_solid.md` D6-07 (`:603-822`);
  `00_priors.md` E-26 (`:286-296`); `07_test_gaps.md` Part 7.B D6-07 row /
  PT-20c (`:3271`); `09_open_questions.md` Q-01 / A-01 (formalized as
  E-26).
- **Open questions:** none. A-01 (developer-confirmed 2026-05-13) is
  formalized as the locked E-26; the 24 banker's sites and the 19-file
  redeclaration are findings against E-26, not an open intent question.
  The `savings_goal_service` ROUND_CEILING site is the documented
  sanctioned exception, explicitly not a finding.
- **Remediation direction:** Introduce the single `app/utils/money.py`
  `round_money(x)` (2dp, ROUND_HALF_UP, the only default) plus
  explicitly-named sanctioned variants (`round_money_ceiling` for the
  savings-goal case), route every one of the 19 `TWO_PLACES`
  declarations and every boundary `.quantize()` through it, and leave
  intermediates at full precision -- exactly E-26's stated end state.
- **Blast radius / symptom link:** up to a one-cent error on any
  displayed dollar produced through one of the 24 bare sites -- the
  investment/retirement projection chart balances (`investment.py:223,
  226,535,538,580`), the savings debt summary
  `total_debt`/`total_monthly_payments`
  (`savings_dashboard_service.py:872-873`), and the retirement monthly
  investment-income figure (`retirement_dashboard_service.py:240`); no
  single developer-reported symptom (latent computed drift; the
  structural root under the Phase-3/5 DIVERGE-against-E-26 record).

### HIGH-05 -- No single loan-obligation / committed-monthly aggregator: expired-template overcount + 26/12 factor redeclared at four sites

- **Severity + rubric justification:** HIGH. Rubric: "stored/computed drift
  ... sufficient to produce a wrong dollar under realistic inputs." E-24
  proven defect #1: `compute_committed_monthly` omits the
  `rule.end_date < today` filter that all three `/obligations` loops apply,
  so an expired recurring template keeps contributing to the emergency-fund
  baseline and per-goal contribution floors on `/savings` indefinitely while
  `/obligations` correctly drops it -- a wrong dollar under realistic inputs
  (any user with a since-expired recurring expense/transfer template). Plus
  the 26/12 biweekly-to-monthly factor is redeclared at four sites (drift if
  any is edited independently). No single developer-reported symptom; HIGH.
- **Category:** drift, DRY, source-of-truth.
- **Plain-language description:** Two related duplications under one root.
  (1) The "total committed monthly" amount is computed by four near-
  identical loops -- three in `/obligations` and one in
  `compute_committed_monthly` -- but only the three `/obligations` loops
  skip a template whose recurrence end date is in the past;
  `compute_committed_monthly` does not, so an expired recurring expense or
  transfer keeps inflating the emergency-fund baseline and every per-goal
  contribution floor on `/savings` forever, while the `/obligations` page
  correctly excludes it. The same obligation is therefore two different
  numbers on two pages. (2) The biweekly-to-monthly conversion factor
  (26/12) is named once but re-inlined as a literal at three other sites,
  so editing one and not the others would silently drift the monthly
  equivalent across `/savings`, `/obligations`, and the retirement-gap
  projection.
- **Subsumes:**
  - Phase 6: **D6-05** (E-24; three structurally-identical inline loops; the
    26/12 factor redeclared at four sites; `compute_committed_monthly` missing
    the `end_date` filter).
  - Resolved intent: Q-12/A-12 (E-24).
  - PT pointer: PT-20a.
- **Governing E-NN:** E-24 (one canonical monthly-equivalent aggregator with
  the shared ONCE / `end_date` filter; template-to-loan FK with delta
  surfacing; de-duplicated 26/12 factor).
- **Evidence (re-resolved to live source this session, key line quoted):**
  - `app/services/savings_goal_service.py:287-328`
    `compute_committed_monthly` (read in full this session): the
    accumulation loop `:310` `for template in list(expense_templates) +
    list(transfer_templates):` skips only `amount is None or == 0`
    (`:313`) and `rule is None` (`:318`), then `:322 monthly =
    amount_to_monthly(...)` / `:325-326 if monthly is not None: total +=
    monthly` -- **there is no `rule.end_date < date.today()` guard
    anywhere in the function body**.
  - `app/routes/obligations.py:335` -- `if rule.end_date is not None and
    rule.end_date < date.today():` / `continue` (the expired-template
    filter), repeated at `:358` and `:380` (the income and transfer
    loops); `obligations.py:262 def summary():`. The filter the three
    route loops apply and `compute_committed_monthly` omits, confirmed
    live.
  - 26/12 factor: `savings_goal_service.py:17` `_PAY_PERIODS_PER_YEAR =
    Decimal("26")` (named, with `_MONTHS_PER_YEAR` `:18`) vs the in-file
    inline `:169` `(months * Decimal("26") / Decimal("12"))` and the
    further re-inlinings `savings_dashboard_service.py:171,765`,
    `retirement_gap_calculator.py:69` (carried from D6-05;
    `savings_goal_service.py:17,169` re-resolved live this session).
- **Phase-doc pointers:** `06_dry_solid.md` D6-05 (`:374-481`);
  `00_priors.md` E-24 (`:260-274`); `03_consistency.md` W-251 tag
  (`:5328`), F-008 (`:580-645`); `07_test_gaps.md` Part 7.B D6-05 row /
  PT-20a (`:3269`); `09_open_questions.md` Q-12 / A-12.
- **Open questions:** none. Q-12/A-12 is ANSWERED and locked by E-24 (one
  canonical monthly-equivalent aggregator with the shared skip-ONCE /
  skip-`end_date < today` filter; the 26/12 factor imported not
  re-inlined), consumed as this cluster's governing end state and NOT
  re-litigated; the E-24 distinct-question paths
  (`obligations.summary` vs `dashboard._compute_cash_runway` vs
  `savings_dashboard._compute_debt_summary`) are explicitly out of this
  DRY root.
- **Remediation direction:** Per E-24, one canonical monthly-equivalent
  aggregator applying the shared skip-ONCE / skip-`end_date < today`
  filter, called by all three `/obligations` loops and by
  `compute_committed_monthly`, with `_PAY_PERIODS_PER_YEAR` /
  `_MONTHS_PER_YEAR` imported at every 26/12 site rather than re-inlined.
- **Blast radius / symptom link:** the emergency-fund baseline and every
  per-goal contribution floor on `/savings` are overstated for any user
  with an expired recurring expense/transfer template (while
  `/obligations` shows the correct lower figure), and the biweekly->
  monthly equivalent can drift across `/savings`/`/obligations`/
  retirement-gap if any inlined factor is edited independently; no
  developer-reported symptom (latent; the E-24 "one obligation, two
  unreconciled representations").

### HIGH-06 -- Schema-vs-DB-CHECK domain mismatches; reachable silent rate default on first save; 0-vs-NULL three-way divergence

- **Severity + rubric justification:** HIGH. Rubric: "stored/computed drift
  ... sufficient to produce a wrong dollar under realistic inputs" (E-28).
  Confirmed live this session: a first save of interest params that
  omits/blanks `apy` commits a new row whose `server_default="0.04500"`
  materializes a silent 4.5% APY (`interest_params.py:60`), and
  `calculate_interest` treats only `apy <= 0` as "no interest" -- so a user
  who never configured a rate gets real projected interest in the dangerous
  direction (missing -> plausible non-zero, not -> zero). Plus the PA-01
  unwritable `trend_alert_threshold` (Marshmallow `Range(min=1,max=100)` vs
  DB `CHECK(0..1)`: only the value 1 satisfies both), the PA-02
  schema-vs-DB percentage/fraction domain mismatch, and the tri-consumer
  0-vs-None `annual_contribution_limit` divergence. Reachable wrong number
  under realistic first-save input; HIGH (no single developer-reported
  symptom).
- **Category:** source-of-truth, drift.
- **Plain-language description:** Several stored rate/threshold columns have
  a validation domain that disagrees with the database CHECK or behaves
  wrongly when blank or zero. (1) `trend_alert_threshold`: the Marshmallow
  schema accepts integers 1-100 but the DB CHECK requires 0-1, so every
  value the form accepts except exactly 1 is rejected by the database -- the
  field is effectively unwritable. (2) The rate fields (apy, interest_rate,
  several percentage rates) are validated as a 0-100 percentage in
  Marshmallow but stored as a 0-1 decimal fraction under the DB CHECK; the
  handler papers this over with a /100 divide, so the two domains are
  inconsistent by design and a path that stores without the divide would
  violate the CHECK. (3) `apy` is not required on the interest-params update
  schema and blank strings are stripped, so a first save that omits it
  constructs a row that silently inherits the `server_default` 4.5% -- real
  interest the user never configured. (4) A stored `annual_contribution_
  limit` of 0 means three different things across three consumers (card
  hidden / $500 default / hard-zero cap). (5) The Python-side
  `default=0.07000` on `assumed_annual_return` is a float literal (a
  Decimal-from-float code-quality defect; the persisted value is unaffected
  because Postgres re-quantizes).
- **Subsumes:**
  - Prior-audit: **PA-01** (`trend_alert_threshold` Marshmallow
    `Range(1,100)` vs DB `CHECK(0..1)` -- unwritable field); **PA-02** (rate
    fields `Range(0,100)` percentage vs DB `CHECK(0..1)` decimal).
  - Phase 4: `budget.interest_params.apy` AUTHORITATIVE (write-path silent
    4.5% default hazard, Q-24 #2); `budget.investment_params.
    annual_contribution_limit` AUTHORITATIVE (tri-consumer 0/None divergence,
    Q-24 #3); `budget.investment_params.assumed_annual_return` float Python
    default (E-11/E-28 facet; the 0-vs-None read hazard is CRIT-04).
  - Resolved intent: Q-24/A-24 (E-28); the `assumed_annual_return` float
    default recorded by E-28 as a finding, not an intent question.
- **Governing E-NN:** E-28 (one DB-enforced domain consistent with the
  Marshmallow schema and any sibling; the consolidated DB-CHECK pass).
- **Evidence (re-resolved to live source this session, key line quoted):**
  - PA-01: `app/schemas/validation.py:1787-1788` `trend_alert_threshold =
    fields.Integer(validate=validate.Range(min=1, max=100))` vs
    `app/models/user.py:198` CHECK `"trend_alert_threshold >= 0 AND
    trend_alert_threshold <= 1"` -- the schema admits 1..100 while the DB
    CHECK admits 0..1; only the value 1 satisfies both, so the field is
    effectively unwritable.
  - PA-02: `validation.py:1397-1399` / `:1414-1416` `apy = fields.Decimal(
    ... validate=validate.Range(min=0, max=100))`, `:1445` `interest_rate
    ... Range(min=0, max=100)`, `:822/830/834/854` rate fields
    `Range(min=0, max=100)` vs DB CHECK `apy >= 0 AND apy <= 1`
    (`app/models/interest_params.py:34`) and `assumed_annual_return >= -1
    AND assumed_annual_return <= 1` (`app/models/investment_params.py:22`)
    -- a 0-100 percentage schema domain vs a 0-1 (or -1..1) fraction DB
    domain, reconciled only by an in-handler /100.
  - `app/models/interest_params.py:60` -- `apy = db.Column(db.Numeric(7,
    5), nullable=False, server_default="0.04500")` (the silent 4.5% on a
    first save that omits `apy`; the update schema does not require it).
  - `app/models/investment_params.py:80-82` -- `assumed_annual_return =
    db.Column(db.Numeric(7, 5), nullable=False, default=0.07000,
    server_default=db.text("0.07000"))` (Python float `default=0.07000`,
    the E-11/E-28 code-quality facet); `:84` `annual_contribution_limit =
    db.Column(db.Numeric(12, 2), nullable=True)`, CHECK `:31-33`
    `annual_contribution_limit IS NULL OR annual_contribution_limit >= 0`
    (a stored 0 is valid, distinct from NULL).
  - Tri-consumer 0-vs-None: `app/routes/investment.py:231` `if params and
    params.annual_contribution_limit:` (truthiness; card suppressed),
    `:305` `if params.annual_contribution_limit:` (truthiness), `:667`
    `if inv_params and inv_params.annual_contribution_limit:` (truthiness;
    $500 default) vs `app/services/growth_engine.py:206` `if
    annual_contribution_limit is not None:` (is-not-None; 0 honored as a
    hard zero cap) -- one stored 0 read three ways.
- **Phase-doc pointers:** `04_source_of_truth.md` Family C `apy`
  (`:1382-1421`), `assumed_annual_return` (`:1423-1464`),
  `annual_contribution_limit` (`:1466-1503`); `00_priors.md` PA-01/PA-02;
  `04_source_of_truth.md` consolidated D3 (`:2099-2127`);
  `09_open_questions.md` Q-24 / A-24.
- **Open questions:** none blocking. Q-24/A-24 is ANSWERED and locked by
  E-28 (one DB-enforced domain consistent with the Marshmallow schema and
  any sibling; the consolidated DB-CHECK pass); the
  `assumed_annual_return` float default is recorded by E-28 as a finding,
  not an intent question. The 0-vs-None `assumed_annual_return` *read*
  hazard is CRIT-04's (cross-ref); only the float-default / domain-
  mismatch facets are HIGH-06's.
- **Remediation direction:** Per E-28, reconcile each Marshmallow domain
  with its DB CHECK (one consistent unit and range, no schema value the
  CHECK rejects), require/normalize `apy` so a first save cannot inherit
  the silent server-default, normalize `annual_contribution_limit`
  0-vs-NULL to one meaning across consumers, and construct the
  `assumed_annual_return` default from a string.
- **Blast radius / symptom link:** an unconfigured-rate first save
  silently projects interest at 4.5% the user never set;
  `trend_alert_threshold` cannot be saved at any value but 1; a stored
  `annual_contribution_limit = 0` produces three contradictory behaviors;
  no developer-reported symptom (latent reachable wrong number /
  unwritable field).

### HIGH-07 -- Employer-match figure diverges across the card, the chart, and the year-end summary

- **Severity + rubric justification:** HIGH. Rubric: "stored/computed drift
  ... sufficient to produce a wrong dollar under realistic inputs."
  Confirmed live: the investment dashboard "Employer contribution per
  period" card calls the canonical `calculate_employer_contribution` with
  the **uncapped** `periodic_contribution` (`investment.py:183 ->
  187-189`) while the growth chart's employer line and the year-end
  `year_summary_employer_total` feed it the **limit-capped** contribution
  (`growth_engine.py:258-265`). For a match-type employer on an account near
  its annual contribution limit the card overstates the per-period employer
  match relative to both the chart and the year-end total (F-043 worked
  example: in the last limit-binding period card $240.00 vs chart/year-end
  $100.00) -- one obligation, three surfaces, no error. HIGH (realistic
  wrong dollar, no developer-reported symptom).
- **Category:** drift, definition.
- **Plain-language description:** The per-period employer-match figure is
  computed three places that disagree when the annual contribution limit
  binds. All three call the same canonical employer-match function, but the
  investment dashboard card passes the employee's full uncapped per-period
  contribution, while the growth chart's employer line and the year-end
  employer total pass the contribution after it has been capped at the
  remaining annual limit. So for a matching employer on an account near its
  annual limit, the dashboard card shows a larger per-period employer match
  than the chart and the year-end summary for the same account and period,
  with no error and no label -- the user sees one employer number on the
  card and a different (lower) one on the chart and year-end tab.
- **Subsumes:**
  - Phase 3: **F-043** employer_contribution DIVERGE (SILENT; A-01 caveat);
    **F-055** year_summary_employer_total DIVERGE (SILENT+PLAN; inherits
    F-043; Q-15).
  - Phase 7: `employer_contribution` / `year_summary_employer_total`
    non-COVERED.
  - PT pointer: PT-19.
- **Governing E-NN:** NONE -> SILENT/PLAN definition drift; A-01 caveat.
- **Evidence (re-resolved to live source this session, key line quoted):**
  - `app/services/growth_engine.py:91` -- `def
    calculate_employer_contribution(employer_params,
    employee_contribution):` (the SOLE producer; both surfaces delegate to
    it).
  - `app/routes/investment.py:183` -- `periodic_contribution =
    inputs.periodic_contribution` (uncapped) then `:185-189`
    `employer_contribution_per_period =
    growth_engine.calculate_employer_contribution(employer_params,
    periodic_contribution)` -- the card feeds the UNCAPPED employee
    contribution (Phase-3 cited `:188`; live is the `:187-189` call on
    the `:183` uncapped value -- minor line note, behaviour unchanged).
  - `app/services/growth_engine.py:258-265` -- `if remaining_limit is
    not None:` / `contribution = min(period_contrib_amount,
    remaining_limit)` ... `:265 employer_contribution =
    calculate_employer_contribution(employer_params, contribution)` --
    the chart line feeds the LIMIT-CAPPED contribution;
    `year_end_summary_service.py:1027 _project_investment_for_year` sums
    this same capped per-period value (F-055).
- **Phase-doc pointers:** `03_consistency.md` F-043 (`:3601-3688`),
  F-055 (`:4037-4094`); `02_concepts.md` P2-d employer paths
  (`:2154-2160`, `:2756-2761`); `07_test_gaps.md`
  `employer_contribution`/`year_summary_employer_total` non-COVERED +
  PT-19; `00_priors.md` PA-04/PA-05 (the co-located fallback chain),
  PA-29 (growth-engine match/flat directional-only).
- **Open questions:** none new (F-043 "Open questions for the developer:
  none new"). The card-vs-chart cap divergence is provable from code;
  whether the card *should* show capped or uncapped is a remediation
  choice, not a "which is the code's intent" ambiguity (both behaviors
  exist and disagree, which is the finding). F-055's per-account
  dispatcher axis is Q-15-blocked, but that facet's primary home is the
  net_worth dispatcher under CRIT-01 (F-006 cross-ref), not this
  cluster's verdict.
- **Remediation direction:** Decide whether the dashboard card should
  display the limit-capped per-period employer match (consistent with the
  chart and year-end total) and, if so, pass the capped contribution to
  `calculate_employer_contribution` at `investment.py:187` so all three
  surfaces read one value.
- **Blast radius / symptom link:** the "Employer contribution per period"
  card on the investment dashboard overstates the match relative to the
  growth chart's employer line and the year-end
  `year_summary_employer_total` for any match-type employer on an account
  near its annual contribution limit; no developer-reported symptom
  (latent SILENT divergence).

### HIGH-08 -- Loan amortization per-period and summary figures diverge across surfaces

- **Severity + rubric justification:** HIGH. Rubric: "stored/computed drift
  ... sufficient to produce a wrong dollar under realistic inputs"; E-18
  family, downstream of CRIT-02's no-single-resolver root. Six loan/debt
  amortization figures each diverge across surfaces from a provable code
  difference: F-017 per-period principal (B sums raw escrow-inclusive shadow
  income vs A/C A-06-prepared: $500 escrow wrongly counted to principal in
  the worked example), F-018 per-period interest (raw vs A-06-prepared
  schedule input), F-020 total_interest (life-of-loan vs calendar-year vs
  strategy-base), F-021 interest_saved (the `loan.py:968`
  banker's-vs-HALF_UP half-cent, the A-01 site; cross-link HIGH-04), F-022
  months_saved (four distinct month quantities one token; render-slot
  reuse), F-023 payoff_date (`calculate_payoff_by_date` has no
  payments/anchor parameter -> ARM mismatch). Realistic wrong numbers on
  loan/debt pages; the phase8_plan P8-b/P8-c split deliberately separates
  these latent figures from CRIT-02's symptom-bearing triple. HIGH (no
  developer-reported symptom).
- **Category:** drift, definition.
- **Plain-language description:** Six figures on the loan and debt-strategy
  pages are each computed more than one way and the ways disagree, all
  downstream of the same "no single loan resolver" root as CRIT-02. The
  per-period principal split differs because one path subtracts escrow
  before splitting and another counts the whole escrow-inclusive payment
  toward principal (a $500/month error in the worked example). The
  per-period and total interest differ for the same reason plus a
  life-of-loan vs calendar-year vs debt-strategy scope difference.
  "Interest saved" rounds with banker's rounding in one place and
  round-half-up in another (a half-cent boundary divergence; the same site
  as HIGH-04). "Months saved" is four different month quantities sharing one
  render slot (acceleration vs committed vs refinance break-even vs
  strategy). "Payoff date" diverges for an ARM because the target-date
  function structurally cannot take the user-verified anchor. None of these
  is a developer-reported symptom, but each is a wrong displayed number
  under realistic inputs.
- **Subsumes:**
  - Phase 3: **F-017** principal_paid_per_period DIVERGE (SCOPE; A-05/Q-09);
    **F-018** interest_paid_per_period DIVERGE (DEFINITION; A-06/C-05);
    **F-020** total_interest DIVERGE (DEFINITION by design; A-06/C-05);
    **F-021** interest_saved DIVERGE (ROUNDING; A-05); **F-022** months_saved
    DIVERGE (DEFINITION); **F-023** payoff_date DIVERGE (SILENT; A-05).
  - Resolved intent: A-05 addendum resolved by E-18; A-06 (Q-06).
  - PT pointers: PT-10, PT-11, PT-12, PT-13, PT-14, PT-15.
- **Governing E-NN:** E-18 (same single-resolver root as CRIT-02; A-06 for
  the by-design definition facets of F-018/F-020).
- **Evidence (re-resolved to live source this session, key line quoted):**
  - F-017 interest-base divergence: `app/services/balance_calculator.py:253`
    `running_principal = Decimal(str(anchor_balance))` / `:274`
    `interest_portion = (running_principal * monthly_rate).quantize(` /
    `:277` `principal_portion = total_payment_in - interest_portion` --
    B's interest base is the account anchor and B sums raw escrow-inclusive
    shadow income, vs the A-06-prepared engine schedule (escrow subtracted)
    walked from `original_principal`/anchor in `amortization_engine.py`.
  - F-021 rounding site: `app/routes/loan.py:966-968`
    `committed_interest_saved = (original_interest -
    committed_interest).quantize(Decimal("0.01"))` -- **no `rounding=` ->
    banker's**, vs `app/services/amortization_engine.py:740`
    `interest_saved = total_interest_standard - total_interest_extra` /
    `:749` `interest_saved=interest_saved.quantize(TWO_PLACES,
    ROUND_HALF_UP)` (HALF_UP). Same loan, opposite rounding at a half-cent
    boundary (the A-01 `loan.py:968` site; HIGH-04 register-(b) row 24).
  - F-022 / F-020 / F-023: `loan.py:957-959` `committed_months_saved =
    len(original_schedule) - len(committed_schedule)` (a different pair
    than `amortization_engine.py:739 months_saved = len(standard) -
    len(accelerated)`); `amortization_engine.py:642 total_interest = sum(`
    / `:645 payoff_date = schedule[-1].payment_date`;
    `amortization_engine.py:753 def calculate_payoff_by_date(
    current_principal, annual_rate, remaining_months, target_date,
    origination_date, payment_day, original_principal=None,
    term_months=None, rate_changes=None)` -- the signature has **no
    `payments`, `anchor_balance`, or `anchor_date` parameter**, so for an
    ARM it cannot reproduce A's anchored schedule (F-023 SILENT).
- **Phase-doc pointers:** `03_consistency.md` F-017 (`:1360-1444`), F-018
  (`:1448-1506`), F-020 (`:1569-1629`), F-021 (`:1633-1693`), F-022
  (`:1697-1753`), F-023 (`:1757-1815`); `00_priors.md` E-18, A-05/A-06;
  `07_test_gaps.md` PT-10..PT-15; `09_open_questions.md` A-05 addendum /
  A-06 (Q-06).
- **Open questions:** none blocking. A-05 (the `monthly_payment`
  input-divergence addendum) is resolved by E-18 (the same single-resolver
  root as CRIT-02), and A-06 (Q-06; both the escrow-subtraction and
  biweekly-redistribution layers apply, the bare per-row sum is incomplete)
  is ANSWERED -- both consumed as this cluster's governing end state and
  NOT re-litigated; the F-018/F-020 DEFINITION-by-design facets are
  governed, not open questions.
- **Remediation direction:** The same E-18 single pure loan resolver that
  fixes CRIT-02 -- one event-derived (balance, monthly_payment, schedule)
  source consumed identically by the dashboard, year-end, savings PITI,
  debt-strategy, and refinance surfaces, with A-06-prepared payments
  everywhere and the one money-rounding helper (HIGH-04) -- collapses these
  six per-surface divergences; distinctly-defined "months" figures must
  each be labeled so the refinance break-even is not compared to an
  acceleration.
- **Blast radius / symptom link:** wrong per-period principal/interest,
  total interest, interest saved, months saved, and ARM payoff date across
  the loan dashboard, year-end summary, debt-strategy, and refinance
  surfaces; no developer-reported symptom (latent; the E-18-family figures
  the phase8_plan split deliberately separated from CRIT-02's
  symptom-bearing triple).

## MEDIUM tier (provisional)

### MED-01 -- SOLID structure aggregate: route monoliths, dual per-account dispatcher, hardcoded enum dispatch, whole-dict ISP, DIP duck-typing

- **Severity + rubric justification:** MEDIUM. Rubric: "DRY/SOLID violations
  ... with no current wrong number" (section 3.3 MEDIUM). Every constituent is
  Phase-6 `NONE -> structural-only` with an explicit Phase-6 "no observed drift
  yet" blast-radius line; no Phase-3/4/5 worked example produces a wrong
  displayed dollar through any of these. The one latent money path is the
  S6-01 `investment.py:444-523` ~vs~ `:120-218` near-verbatim
  contribution-timeline re-implementation across the two routes: a fix applied
  to one and not the other would drift the growth-chart balance from the
  dashboard balance for the same account -- realistic but unexercised, so
  MEDIUM (structure is the substrate, no cited wrong dollar), not HIGH.
- **Category:** SOLID.
- **Plain-language description:** Five SOLID-structure issues share one root:
  account-projection and dashboard-assembly logic is not factored behind
  single, declared seams. (1) The savings dashboard route was correctly
  reduced to a 4-line delegator into `savings_dashboard_service`, but the
  identical "route owns HTTP + many inline ORM queries + projection math"
  monolith persists un-extracted in the investment dashboard and growth-chart
  routes, with the contribution-timeline body duplicated between them. (2) Two
  independent per-account-type dispatchers ("which calculator produces this
  account's balances") exist with divergent branch order and dispatch key, so
  the same loan can take two code paths to "its projected balance." (3) The
  payroll-deduction-vs-transfer contribution path is decided by a hardcoded
  two-member account-type enum set rather than a metadata flag, so a new
  employer-plan type is silently mis-routed. (4) An 11-key context dict is
  passed whole into helpers that read 1-2 keys, hiding each helper's true
  dependency. (5) The loan engine duck-types a model-shaped object instead of
  a declared plain-data input. None of these changes a number today; together
  they are the structural substrate under the CRIT-01/CRIT-02 drift families
  and the maintainability surface where a one-sided fix would introduce one.
- **Subsumes:** Phase 6 **S6-01** (`investment.py` dashboard/growth_chart
  route monolith; the carried 470-line `savings.py:dashboard` tag proven stale
  -- R-4 -- and carried as Phase 6's corrected state), **S6-03** (two
  per-account-type calculator dispatchers + a third partial `needs_setup`
  copy; the dual dispatcher behind F-001/F-008, recorded structural-only by
  Phase 6), **S6-04** (`_DEDUCTION_PATH_TYPES` hardcoded enum), **S6-05**
  (verify-confirm: metadata-flag dispatch is the dominant pattern, several
  `planned-per-plan` rows in fact complete, one residual type-identity lookup
  with no governing flag -- a negative/structural-completeness record, no
  wrong number), **S6-06** (11-key `ctx` / 4-key `base_args` ISP), **S6-07**
  (`get_loan_projection` DIP duck-typing of a `LoanParams`-shaped object).
  PT pointers (Part 8.B B.11): PT-05 (S6-03 dual-dispatcher equivalence),
  PT-20f/PT-20g (the B6-01/B6-02 enforced-boundary guards, co-located with
  the structural-guard test set).
- **Governing E-NN:** NONE -> structural-only. No E-18..E-28 governs route
  layering, dispatcher count, the deduction-path flag, the parameter surface,
  or the loan-input DTO; remediation stays structural and invents no
  single-source target (contract item 6; Phase-6 G7/G8).
- **Evidence (re-resolved to live source this session, key line quoted):**
  - S6-01: `wc -l app/routes/savings.py` = **288**; `app/routes/savings.py:110-113`
    `def dashboard():` is a 4-line delegator -- `:112` `ctx =
    savings_dashboard_service.compute_dashboard_data(current_user.id)`,
    `:113 return render_template("savings/dashboard.html", **ctx)`; contrast
    `app/services/savings_dashboard_service.py:61` `def
    compute_dashboard_data(user_id):` (the extracted service the
    carried-stale 470-line tag's site became -- R-4). The relocated root:
    `wc -l app/routes/investment.py` = **804**; `:66` `def
    dashboard(account_id):` and `:366` `def growth_chart(account_id):` --
    no `investment_dashboard_service` equivalent exists.
  - S6-03: `app/services/savings_dashboard_service.py:294` `def
    _compute_account_projections(` and `app/services/year_end_summary_service.py:2036`
    `def _get_account_balance_map(` -- two dispatchers answering the same
    question.
  - S6-04: `app/routes/investment.py:58` `_DEDUCTION_PATH_TYPES =
    frozenset([AcctTypeEnum.K401, AcctTypeEnum.ROTH_401K])` with the
    `:56-57` comment `# ... If new employer-plan types are added, # update
    this set.` (the open-closed smell, named in the code); use at `:290`
    `ref_cache.acct_type_id(t) for t in _DEDUCTION_PATH_TYPES`.
  - S6-06: `app/services/year_end_summary_service.py:90` `def
    _load_common_data(`; the return dict `:166-185` carries **exactly 11
    keys** (`year_periods` ... `salary_gross_biweekly`), passed whole into
    helpers reading 1-2.
  - S6-07: `app/services/amortization_engine.py:864` `def
    get_loan_projection(` -- reads 7 attributes off a duck-typed
    model-shaped `params`, not a declared `LoanInputs` DTO (the negative to
    the `PaymentRecord` positive control).
- **Phase-doc pointers:** `06_dry_solid.md` S6-01 (`:1189-1276`), S6-03
  (`:1312-1397`), S6-04 (`:1398-1461`), S6-05 (`:1462-1539`), S6-06
  (`:1540-1601`), S6-07 (`:1602-1685`), Phase-6 handoff (`:2191-2268`);
  `07_test_gaps.md` Part 7.B S6-03 row (`:3261+`), PT-05/PT-20f/PT-20g;
  `05_symptoms.md:1708-1710` (the dual-dispatch drift surface Phase 5 handed
  forward).
- **Open questions:** none. Every member is a Phase-6 structural finding
  with no governing E-NN and no developer-decision question; S6-05 is a
  verify-confirm negative recorded so the OCP picture is complete.
- **Remediation direction:** Extract the investment dashboard/growth-chart
  data-assembly + projection bodies into one `investment_dashboard_service`
  mirroring the already-correct `savings_dashboard_service` shape, collapse
  the two-plus per-account dispatchers and the `needs_setup` ladder into one
  flag-driven `account_projection` dispatcher, key the deduction path on an
  account-type metadata attribute, pass each helper the 1-2 fields it reads,
  and give `get_loan_projection` a declared `LoanInputs` DTO -- structural
  only, no behavioral change.
- **Blast radius / symptom link:** no displayed wrong dollar today; the
  latent money path is the `investment.py:444-523` ~vs~ `:120-218`
  contribution-timeline duplication (a one-sided fix drifts the growth-chart
  balance from the dashboard balance for the same account); no
  developer-reported symptom (latent structural substrate under the CRIT-01/
  CRIT-02 families).

### MED-02 -- Status predicate expressed inline across many files instead of one centralized predicate

- **Severity + rubric justification:** MEDIUM. Rubric: "DRY/SOLID violations
  ... with no current wrong number." The ID comparison itself is E-15-compliant
  (integer IDs, never string `name`), so there is no wrong number today; the
  defect is that the single conceptual predicate "is this a live,
  balance-contributing Projected row" and the tier-2 `[CREDIT, CANCELLED]`
  exclusion set are hand-reproduced in 20+ sites in three structurally
  different forms (Python in-loop skip, SQLAlchemy filter, Jinja constant) plus
  two identically-bodied helpers under different names. A future status-rule
  change applied to some sites and not others would silently drift a balance --
  realistic but unexercised, so MEDIUM not HIGH.
- **Category:** DRY, SOLID.
- **Plain-language description:** Whether a transaction counts toward a balance
  depends on its status, and that rule is written out by hand wherever it is
  needed instead of living in one predicate. The "skip non-Projected rows"
  guard `status_id != projected_id` appears inline in the balance calculator
  (three times), the grid subtotal, and the credit workflow; the
  `status_id == projected_id` query filter is rebuilt in 11 more places; the
  "exclude Credit and Cancelled from balance" set is re-derived twice as two
  helpers with different names but identical bodies, and reproduced inline in
  several services; and the same predicate is hardcoded against Jinja status
  constants across the grid templates (with one grid-vs-mobile row-match
  duplicated byte-for-byte). Every site is individually ID-based and correct
  today, so no number is wrong now; the risk is that a change to the
  status-to-balance rule made in one place and missed in another silently
  produces a wrong balance, with no error.
- **Subsumes:** Phase 6 **D6-09** (E-15 family; the `!= projected_id` skip at
  `balance_calculator.py:365/411/443`, `grid.py:269`, `credit_workflow.py:192`;
  the `== projected_id` filter at 11 sites; the `[CREDIT, CANCELLED]` set
  re-centralized twice + inline; the Jinja-constant reproductions). PT pointer
  (Part 8.B B.11): PT-20e (the consolidation guard; the status-boolean VALUE
  matrix is already pinned by `test_status_boolean_attributes`, so this is the
  single-source-consolidation half only).
- **Governing E-NN:** E-15 (reference-table conditionals compare integer IDs;
  satisfied at every site). The centralization of the predicate has no
  governing E-NN -- `NONE -> structural-only` for the consolidation half (no
  E-NN names a single status-predicate helper); recorded as the E-15-family
  DRY finding, not an E-15 violation.
- **Evidence (re-resolved to live source this session, key line quoted):**
  - `app/services/balance_calculator.py:365` / `:411` / `:443` -- three
    occurrences of `if txn.status_id != projected_id:` (entry-formula gate,
    `_sum_remaining`, `_sum_all`), each binding `projected_id =
    ref_cache.status_id(StatusEnum.PROJECTED)` locally.
  - `app/services/year_end_summary_service.py:655` `excluded_ids =
    _get_excluded_status_ids()` -- a private helper returning the
    `[CREDIT, CANCELLED]` set; `app/services/budget_variance_service.py:207`
    `excluded_status_ids = [` -- the same set rebuilt under a different name
    (the "two helpers, different names, identical bodies" hazard); plus the
    `Status.excludes_from_balance.is_(False)` reproductions at
    `year_end_summary_service.py:1016,1097`.
- **Phase-doc pointers:** `06_dry_solid.md` D6-09 (`:928-997`), Phase-6
  handoff (`:2191-2268`); `financial_calculation_audit_plan.md:636-637`
  (the audit-plan names this pattern verbatim); `00_priors.md` E-15
  (`:374`); `07_test_gaps.md` Part 7.B D6-09 row (`:3273`; the only
  PARTIAL/YES -- value matrix pinned, consolidation not), PT-20e.
- **Open questions:** none. E-15 is a locked standard, not an open intent
  question; the value matrix is already test-pinned.
- **Remediation direction:** Introduce one centralized status predicate
  (e.g. `Status.excludes_from_balance` / a `is_balance_contributing(txn)`
  helper and a single `EXCLUDED_STATUS_IDS` accessor) and route every
  inline Python skip, every SQLAlchemy filter, every Jinja conditional, and
  both `[CREDIT, CANCELLED]` helpers through it -- the E-15-family DRY end
  state, ID-based throughout.
- **Blast radius / symptom link:** no displayed wrong dollar today (every
  site is ID-correct); the latent risk is a one-sided status-rule change
  silently drifting a balance; no developer-reported symptom (latent
  DRY/structure).

### MED-03 -- Entry-tracked bill row presents one row with two undisclosed anchor bases (cross-anchor inconsistency)

- **Severity + rubric justification:** MEDIUM. Rubric: "definition ambiguity
  in non-customer-facing places." The dashboard bill row shows its amount cell
  from `txn.effective_amount` (= `actual_amount` for a settled entry-tracked
  txn) while its `entry_remaining`/`entry_over_budget` on the SAME row anchor
  on `estimated_amount` -- one row, two undisclosed bases. Phase 3 verdicted
  the entry-progress base UNKNOWN, blocked on Q-08 (whether "remaining" should
  be estimated- or actual-spend-based); under the cited DONE example
  (`actual=$100`, `estimated=$120`, entries `$80`) the amount cell shows $100
  while "remaining" shows $40 and over-budget reads False -- an internally
  inconsistent dashboard row, not a hand-derived wrong balance on a budgeting
  page, so MEDIUM (definition ambiguity), recorded WITH the blocking Q and NOT
  resolved (contract item 5).
- **Category:** definition, drift, test gap.
- **Plain-language description:** For an entry-tracked (envelope) bill the
  dashboard renders a single row whose dollar amount and whose
  "remaining/over-budget" progress are computed from two different bases that
  the row does not disclose. The amount cell uses the transaction's effective
  amount -- which for a Paid/Settled txn is the entry-derived actual -- while
  the same row's `entry_remaining` and `entry_over_budget` are computed as
  `estimated_amount - sum(all entries)`, never consulting `actual_amount` or
  the status. So a finished envelope bill can show, on one line, an amount of
  $100 (actual) next to "$40 remaining / not over budget" (from the $120
  estimate), with no indication the two figures answer different questions.
  The three sites that compute entry-remaining (dashboard bill row, companion
  data, entries partial) agree with each other; the inconsistency is between
  the remaining figure and the amount cell within the one row. Whether
  "remaining" should track the plan (estimated) or the actual spend is a
  product decision (Q-08); the undisclosed two-base row is the finding
  regardless of how Q-08 resolves.
- **Subsumes:**
  - Phase 3: **F-028** entry-progress base + cross-anchor inconsistency
    UNKNOWN (Q-08; the cross-anchor row inconsistency holds regardless of
    Q-08, `03_consistency.md:2313-2319`); **F-056** `entry_remaining` sub
    UNKNOWN (Q-08) -- the `entry_sum_total` sub of F-056 is
    NOT-A-FINDING:AGREE (split row, Part 8.B B.1).
  - Phase 7: `entry_remaining` BLOCKED-ON-OPEN-QUESTION (Q-08); the
    `goal_progress` GP2 conditional anti-coverage flag (Q-08;
    `07_test_gaps.md:3925` -- the GP2 pins lock the current estimated base
    and become anti-coverage iff Q-08 picks actual-spend; recorded so a
    green bar is not laundered as coverage).
  - Resolved intent: Q-08/A-08 (E-21, consumed as the governing end state,
    NOT re-litigated).
- **Governing E-NN:** E-21 (one coherent plan-vs-actual model: the
  entry-tracked bill row's remaining / over-budget is defined against one
  declared base, disclosed, consistent with the amount cell).
- **Evidence (re-resolved to live source this session, key line quoted):**
  - `app/services/dashboard_service.py:191` -- `"amount":
    txn.effective_amount,` (the bill dict's displayed amount cell = tier-3
    `actual_amount` for a settled entry-tracked txn).
  - `app/services/dashboard_service.py:203` `def
    _entry_progress_fields(txn: Transaction) -> dict:`; `:224-226`
    `is_tracked = (... and txn.template.is_envelope)`; the `entry_remaining`
    / `entry_over_budget` keys (`:233-234`) are computed via
    `compute_remaining` -- **not** status-gated, never consulting
    `actual_amount`.
  - `app/services/entry_service.py:405` `def compute_remaining(` /
    `:406 estimated_amount: Decimal,` / `:409` docstring `Compute remaining
    budget: estimated_amount - sum of ALL entries.` -- it receives only
    `estimated_amount`, so it structurally cannot anchor on `actual_amount`
    or switch on status (the two-base mechanism: amount cell on
    `effective_amount`, remaining on `estimated_amount`, same row).
- **Phase-doc pointers:** `03_consistency.md` F-028 (`:2278-2319`), F-056
  (`:4098+`); `07_test_gaps.md` `entry_remaining` BLOCKED-ON-OPEN-QUESTION
  (`:2221-2225`), `goal_progress` GP2 (`:2280-2284`), 7.F.4 GP2 conditional
  flag (`:3925`); `00_priors.md` E-21; `09_open_questions.md` Q-08 / A-08.
- **Open questions:** **Q-08 (UNKNOWN, NOT resolved here -- contract item
  5).** Whether the entry-tracked bill row's "remaining/over-budget" should
  anchor on the plan (`estimated_amount`) or actual spend is the developer
  decision behind F-028 / F-056 / the GP2 anti-coverage flag. Q-08/A-08
  records the governing end state (E-21, one coherent disclosed model)
  consumed as this cluster's end state and NOT re-litigated; the
  cross-anchor row inconsistency holds regardless of how Q-08 resolves
  (`03_consistency.md:2313`). The blocking developer decision is carried
  forward, recorded not resolved.
- **Remediation direction:** Per E-21, anchor the entry-tracked bill row's
  remaining / over-budget and its amount cell on one declared, disclosed
  base (the Q-08-designated plan-vs-actual model), so a single row never
  presents two undisclosed bases.
- **Blast radius / symptom link:** an internally inconsistent dashboard
  bill row (amount cell vs remaining/over-budget computed on different
  bases) for any finished entry-tracked envelope bill whose actual differs
  from its estimate; no developer-reported symptom (latent;
  non-customer-facing-decision definition ambiguity blocked on Q-08).

### MED-04 -- Standards: Jinja and JavaScript compute money (E-16 / E-17)

- **Severity + rubric justification:** MEDIUM. Rubric: "standards violations
  (Jinja/JS arithmetic) numerically consistent today" (section 3.3 MEDIUM,
  refinement). Phase 1 classified the Jinja arithmetic sites as E-16 candidates
  and the JS recompute sites as E-17 candidates; Phase 3 verdicted them
  AGREE-numerically (the template/JS value equals the server value today). No
  cited displayed wrong dollar. The harm is that financial arithmetic in the
  presentation layer is brittle (a `|float` cast through a binary float, a
  client recompute that can silently diverge from the server) and violates the
  coding standard; consistent today, so MEDIUM not HIGH. The most material
  member, the F-027 `effective_amount` `+E-16` facet, is the E-16 face of a
  CRITICAL whose wrong-dollar home is CRIT-01 -- here only the standards facet
  is recorded (cluster severity is per-member rubric, not inherited from the
  cross-ref).
- **Category:** definition (standards).
- **Plain-language description:** Money is computed in templates and in
  browser JavaScript instead of only in Python services, against the project
  standard ("Templates are for display, not computation"; "Monetary values in
  JS are display-only"). The Jinja side spans eleven sites: the grid
  transaction cell and its mobile twin derive `remaining = estimated_amount -
  entries.total` in the template; several loan partials sum or divide money
  inline (per-row outflow, escrow-per-period as `annual / 12`, payoff "new
  total monthly", refinance principal delta and sign-flip) -- the
  `loan/_escrow_list.html:37` `comp.annual_amount|float / 12` site routes the
  Decimal through a binary float before dividing. The JS side has three real
  recompute sites (retirement-gap chart sums `pension + investment` and
  subtracts from pre-retirement; variance tooltip recomputes `act - est`)
  plus three borderline clamps/reduces on financial inputs. Every value
  matches the server today, so nothing is displayed wrong now; the standard
  exists because a presentation-layer recompute drifts silently the moment the
  server formula changes and one copy is missed.
- **Subsumes:** Phase 1 **TA-01..TA-11** (`01_inventory.md:2484-2494`; the
  eleven arithmetic-in-Jinja sites -- TA-01/TA-02 grid `remaining`, TA-03
  per-row outflow, TA-04 `_escrow_list.html:37` annual/12 + `|float`,
  TA-05 payoff total, TA-06/TA-07/TA-08 refinance deltas, TA-09 months/12,
  TA-10/TA-11 rate*100 percent-display); the Phase-1 **3 + 3 JS recompute**
  sites (`01_inventory.md:2709-2721`: JN-01/JN-02 `retirement_gap_chart.js:24-25`,
  JN-03 `chart_variance.js:69`; borderline JN-B1/JN-B2/JN-B3); Phase 6 carried
  **`loan/_escrow_list.html:37`** `comp.annual_amount|float / 12` E-16 site
  (`06_dry_solid.md:2207-2214` handoff -- a single-site
  template-computes-money + `|float`, outside the Phase-6 DRY/SOLID taxonomy,
  explicitly handed to Phase 8 as a standards finding = TA-04, the same site);
  the **E-16 facet of F-027** (`03_consistency.md` F-027 DIVERGE `(+E-16)`;
  the wrong-dollar home is CRIT-01, only the standards facet here).
- **Governing E-NN:** E-16 (no monetary arithmetic in Jinja) + E-17 (no
  monetary arithmetic in JS); both are locked coding standards, not open
  intent questions.
- **Evidence (re-resolved to live source this session, key line quoted):**
  - TA-01: `app/templates/grid/_transaction_cell.html:21` -- `{% set
    remaining = t.estimated_amount - es.total %}` (template subtracts two
    money values).
  - TA-04 / the Phase-6-carried site: `app/templates/loan/_escrow_list.html:37`
    -- `<td class="text-end font-mono">${{ "{:,.2f}".format(comp.annual_amount|float
    / 12) }}</td>` (template divides money AND casts a Decimal through a
    binary `|float` -- the E-16 + `|float` violation Phase 6 handed forward;
    one site, TA-04 == the handoff site).
  - JN-01 / JN-02: `app/static/js/retirement_gap_chart.js:24` `var covered
    = pension + investment;` / `:25 var remaining = Math.max(0,
    preRetirement - covered);` (client adds and subtracts monetary values).
  - JN-03: `app/static/js/chart_variance.js:69` -- `var diff = act -
    est;` (client recomputes per-month variance the server already
    computed).
- **Phase-doc pointers:** `01_inventory.md` 1.3.x.1 TA table
  (`:2476-2494`), 1.4.x.1/1.4.x.2 JS recompute (`:2705-2739`);
  `06_dry_solid.md` `03:1562` reconciliation (`:2062`) + Phase-8 handoff
  (`:2207-2214`); `03_consistency.md` F-027 (the `+E-16` facet; primary
  home CRIT-01); `00_priors.md` E-16 / E-17.
- **Open questions:** none. E-16/E-17 are locked standards (Phase 3
  AGREE-numerically; TA-10/TA-11 rate*100 and TA-06/TA-09 month-count are
  the borderline presentation-only members, recorded for completeness, not
  wrong numbers).
- **Remediation direction:** Move every monetary computation out of Jinja
  and JS into the route/service in Decimal (compute `entry_remaining`,
  `escrow_per_period`, the loan per-row/payoff/refinance figures, and the
  retirement-gap/variance values server-side and pass the finished numbers
  to the template/chart), eliminating the `|float` casts -- exactly the
  E-16/E-17 end state.
- **Blast radius / symptom link:** no displayed wrong dollar today (Phase 3
  AGREE-numerically at every site); latent -- any server-formula change not
  mirrored into the template/JS copy ships a wrong figure silently; no
  developer-reported symptom (the F-027 facet's wrong-dollar is CRIT-01,
  cross-referenced, not double-counted here).

### MED-05 -- Systematic precision / day-count errors in projections

- **Severity + rubric justification:** MEDIUM. Rubric: MEDIUM "definition
  ambiguity in non-customer-facing places" / small systematic error -- both
  members produce a real wrong number under realistic inputs but the magnitude
  is sub-dollar to low-single-dollar per year and both are documented in the
  source as deliberate accepted trade-offs, not silent defects. PA-06: in a
  leap year `interest_projection` divides actual 366 days by a hardcoded
  365-day divisor, overstating daily interest by ~1/365 (~$1.23 per $100,000
  at 4.5% APY for a 14-day period crossing the leap day, ~$0.25/yr per
  $100,000 over a full leap year, per the module's own docstring). PA-07:
  `gross_biweekly = (annual_salary / pay_periods_per_year).quantize(...)`
  leaves a per-cycle rounding residue (e.g. $100,000/26 quantized * 26 =
  $99,999.90, a -$0.10 ~ +$0.12 annual residue) that the annual aggregate
  never reconciles. Not CRITICAL/HIGH (no budgeting-decision dollar is
  materially wrong; cents-to-low-dollars, documented), not LOW (it is a real
  systematic computed error under realistic inputs, not coincidental).
- **Category:** drift.
- **Plain-language description:** Two small, systematic numeric errors in the
  projection math. (1) The savings-interest projection hardcodes a 365-day
  year for daily compounding; US banks use actual/365, so in a leap year the
  366 actual days are divided by 365 and every leap-year daily-interest figure
  is overstated by about a third of a percent of one day's interest -- on the
  order of a dollar per $100K per leap year. (2) The biweekly paycheck is the
  annual salary divided by the pay-period count and rounded to the cent each
  period; multiplying the rounded paycheck back by 26 does not return the exact
  annual salary, and that residue (a few cents to ~$0.12/yr) is never
  reconciled into the annual income aggregate. Both are real, repeatable, and
  flow into `/savings` and forward income projections; both are small and are
  explicitly documented in the source as intentional simplifications, which is
  why this is MEDIUM rather than a silent CRITICAL.
- **Subsumes:** Prior-audit **PA-06** (`00_priors.md:815`;
  `interest_projection` uses 365 in leap years -- overstates daily interest
  ~0.27%, ~$1.23/$100K for a 14-day leap-crossing period); **PA-07**
  (`00_priors.md:816`; biweekly paycheck quantize residue, missing from the
  annual aggregate). Cross-ref Phase 3 **F-041** `apy_interest`
  (NOT-A-FINDING:AGREE for the single engine in B.1; PA-06 is the
  cross-producer 365/366 body referenced in F-041's title, the standards/
  correctness facet carried here).
- **Governing E-NN:** NONE -> correctness (no E-18..E-28 governs the
  day-count convention or the annual rounding-residue reconciliation; this is
  a coding-standards/correctness finding, not a single-source-of-truth one).
- **Evidence (re-resolved to live source this session, key line quoted):**
  - PA-06: `app/services/interest_projection.py:44` -- `DAYS_IN_YEAR =
    Decimal("365")` (the hardcoded divisor); the module docstring
    `:10-31` states verbatim `... hardcodes a 365-day year ... regardless
    of whether the period falls in a leap year ... overstates daily
    interest by approximately 1/365 ... ~$1.23 per $100,000 ... The trade
    is intentional.` -- a documented accepted trade-off, the MEDIUM
    justification.
  - PA-07: `app/services/paycheck_calculator.py:133` -- `gross_biweekly =
    (annual_salary / pay_periods_per_year).quantize(` (per-period
    quantize); the docstring `:22-26` works the residue explicitly
    (`$100,000 / 26 = $3,846.1538... -> $3,846.15 * 26 = $99,999.90`);
    `:132 pay_periods_per_year = profile.pay_periods_per_year or 26`. No
    annual reconciliation of the residue exists.
- **Phase-doc pointers:** `00_priors.md` PA-06 / PA-07 (`:815-816`);
  `03_consistency.md` F-041 (`apy_interest` single-engine AGREE; the
  365/366 cross-producer note); the `interest_projection.py` /
  `paycheck_calculator.py` module docstrings (the documented trade-offs).
- **Open questions:** none. Both are documented accepted trade-offs with a
  worked magnitude in the source; the finding is that the trade-off is
  unfixed and the paycheck residue is unreconciled, not an open intent
  question.
- **Remediation direction:** Thread the actual day count (366 in leap
  years) into the interest-projection divisor and reconcile the biweekly
  rounding residue into the annual income aggregate (e.g. true-up the final
  period or carry the residue), so leap-year interest and annual gross are
  exact.
- **Blast radius / symptom link:** leap-year savings-interest figures
  overstated ~$1/$100K/yr on `/savings` and the chart series; annual gross
  understated/overstated by the biweekly residue (cents to ~$0.12/yr) in
  income projections; no developer-reported symptom (latent, small,
  documented systematic error).

### MED-06 -- Off-engine DTI gross income denominator diverges with an applicable raise

- **Severity + rubric justification:** MEDIUM. Rubric: MEDIUM "definition
  ambiguity in non-customer-facing places" -- the savings-dashboard DTI uses
  an off-engine gross-income denominator (`salary_gross_biweekly * 26 / 12`)
  instead of the canonical paycheck-engine monthly gross. Phase 3 F-032
  DIVERGE (DEFINITION): with a scheduled recurring raise the two denominators
  differ -- worked example `annual_salary $104,000` + recurring `3%` raise,
  canonical `gross_monthly $8,926.67` / DTI `26.9%` vs the off-engine
  `$8,666.67` / DTI `27.7%` (`03_consistency.md` C2/C3, re-read this
  session). DTI is an informational ratio shown with a label band, not a
  dollar the developer budgets against on the page, so the wrong figure is a
  mis-stated ratio on a non-budgeting-decision surface -- MEDIUM (DEFINITION
  drift, realistic under a raise), not CRITICAL.
- **Category:** drift, definition.
- **Plain-language description:** The debt-to-income ratio on the savings
  dashboard computes monthly gross income as the stored biweekly salary times
  26 divided by 12, a flat conversion that does not run the paycheck engine.
  The paycheck engine applies scheduled raises period-by-period, so for any
  user with an applicable recurring raise the engine's monthly gross and this
  off-engine `biweekly * 26 / 12` disagree, and the DTI ratio (and its label
  band) shown on `/savings` is computed against the wrong denominator. With no
  raise the two coincide, so it is correct for users without scheduled raises;
  with a raise the displayed DTI drifts by roughly a percentage point in the
  cited example, with no error shown. DTI is informational (a ratio with a
  Good/Caution-style label), not a dollar figure used directly for a
  budgeting decision, which is why this is MEDIUM.
- **Subsumes:** Phase 3 **F-032** `paycheck_gross` DIVERGE (DEFINITION;
  off-engine DTI income denominator; the A-01 banker's-mode side, 24-vs-26
  caveat). Phase 7 `paycheck_gross` non-COVERED (no pinned test on the DTI
  gross). PT pointer (Part 8.B B.11): PT-16 (off-engine DTI gross with a
  raise).
- **Governing E-NN:** NONE -> definition-drift; A-01 caveat (the
  developer-confirmed gross-income definition; no E-18..E-28 names a single
  DTI gross producer -- recorded as DEFINITION drift, no invented target,
  contract item 6).
- **Evidence (re-resolved to live source this session, key line quoted):**
  - `app/services/savings_dashboard_service.py:168-172` --
    `gross_biweekly = params["salary_gross_biweekly"]` then `gross_monthly
    = (gross_biweekly * Decimal("26") / Decimal("12")).quantize(_TWO_PLACES,
    rounding=ROUND_HALF_UP)` -- the off-engine flat conversion, NOT the
    paycheck engine; `:173-176` `dti_ratio = (debt_summary[
    "total_monthly_payments"] / gross_monthly * Decimal("100")).quantize(
    Decimal("0.1"), ...)`; `:179 debt_summary["gross_monthly_income"] =
    gross_monthly` (the displayed denominator).
- **Phase-doc pointers:** `03_consistency.md` F-032 (`:2608+` P3-d1
  income/tax family; C2/C3 worked example `:6054`/`:6069`-class, the
  `$8,926.67` vs `$8,666.67` derivation re-read this session);
  `07_test_gaps.md` `paycheck_gross` non-COVERED + PT-16; `09_open_questions.md`
  Q-01..Q-07 / A-01 (the gross-income definition caveat).
- **Open questions:** none blocking. A-01 records the developer-confirmed
  gross-income definition / banker's-mode caveat consumed as the governing
  context; the finding is the off-engine denominator drift under a raise,
  not an open intent question.
- **Remediation direction:** Compute the DTI gross monthly income from the
  same canonical paycheck producer the rest of the app uses (raise-aware),
  not a flat `biweekly * 26 / 12`, so the displayed DTI matches the engine
  for users with scheduled raises.
- **Blast radius / symptom link:** the `/savings` DTI ratio and its label
  band ship wrong for any user with an applicable scheduled raise (~1
  percentage point in the cited example); no developer-reported symptom
  (latent DEFINITION drift on a non-budgeting-decision surface).

### MED-07 -- Test-quality aggregate: directional/loose assertions and missing invariant tests across calc modules

- **Severity + rubric justification:** MEDIUM. Rubric: MEDIUM "missing tests
  for important invariants ... no current wrong number attributable here." This
  is the residue of the prior test-audit corpus whose items neither lock a
  CRITICAL (those route to CRIT-02 PA-28, CRIT-03 PA-21, HIGH-01 PA-10/PA-11)
  nor were superseded by Phase 7. Important: Phase 7 already found several of
  these COVERED with pinned exact-`Decimal` assertions and recorded the
  prior-audit "directional/zero-coverage" tags as findings-against-assumption
  (e.g. pension `test_pension_calculator.py:63-64,146` exact, contradicting
  PA-30's "directional"; spot-checks 7.F.1 #5/#14). MED-07 is therefore the
  genuinely-still-loose subset -- carried as Phase 7's corrected state, not a
  blanket re-assertion of the prior-audit list (the B.8 carry-corrected-state
  precedent). No wrong number is attributable to this cluster (the wrong
  numbers live in the CRITICAL/HIGH clusters; this is the missing regression
  depth around them), so MEDIUM not HIGH.
- **Category:** test gap.
- **Plain-language description:** Across the calculation modules a set of
  tests assert direction ("> 0", "is not None", `isinstance`) or a relationship
  rather than an exact hand-computed value, and several invariants have no test
  at all -- debt-balance assertion depth / sad paths / boundaries /
  status-machine / negatives, HYSA boundary and full-year compounding, paycheck
  and tax negative paths and annual reconciliation, transfer-recurrence
  boundaries, the chart-data service value, the amortization extra-payment and
  growth-engine and (where still loose) directional assertions, plus the
  Phase-7 LOOSE-ONLY `dti_ratio` (the figure is AGREE; the test is the gap).
  Individually none of these is a wrong number; together they are insufficient
  regression depth: a deterministic calc error in these areas would not be
  caught. Phase 7 verified that some prior-audit "directional/zero-coverage"
  claims are now stale (exact pins exist), so this cluster is the part that
  remains genuinely loose, not the whole prior list.
- **Subsumes:** Prior-audit **PA-12, PA-13, PA-14, PA-15, PA-16**
  (debt-balance assertion depth / sad-path / boundary / status-machine /
  negative; `00_priors.md:821-825`), **PA-17, PA-18, PA-19** (HYSA precision
  / boundary / full-year compounding; `:826-828`), **PA-20, PA-22, PA-23,
  PA-24** (paycheck no-exact / negative / tax assertion depth / annual
  reconciliation; `:829,831-833`), **PA-25** (transfer-recurrence boundary;
  `:834`), **PA-26** (chart-data-service value verification; `:835`),
  **PA-27** (amortization extra-payment directional; `:836`), **PA-29**
  (growth-engine directional; `:838`), **PA-30** (pension directional;
  `:839`); Phase 7 LOOSE-ONLY verdicts that do not lock a CRITICAL --
  `dti_ratio` (F-025 AGREE in B.1; the loose-test gap, not the figure). The
  generic PT proposals fold to the cluster each would lock (Part 8.B B.11);
  none is owned here as a finding.
- **Governing E-NN:** NONE -> test-gap (no E-NN governs assertion depth;
  this is a testing-standards finding -- "Service tests must assert computed
  values with exact expectations", `docs/testing-standards.md`).
- **Evidence (re-resolved to live source this session, key line quoted):**
  - The prior-audit corpus is `00_priors.md:821-839` (PA-12..PA-30, the
    `test_audit` rows), with the per-document reconciliation
    `:841` (`test_audit`=21, internal dups removed). Re-read this session.
  - Phase-7 corrected-state confirmation (carried, not re-derived): Phase 7
    7.F.1 spot-check #14 re-resolved `tests/test_services/test_pension_calculator.py:63`
    `annual_benefit == Decimal("38387.50")`, `:64 monthly ==
    Decimal("3198.96")`, `:146 == Decimal("606.80")` -- exact pins that
    make PA-30's "directional" tag stale; #5 similarly for the amortization
    `loan_remaining_months` `300/0/360` exact pins vs PA-28's
    "zero coverage" (PA-28 routes to CRIT-02). MED-07 carries only the
    genuinely-loose remainder; the COVERED ones are Phase-7
    findings-against-assumption, recorded not re-litigated.
  - `dti_ratio` LOOSE-ONLY at `tests/test_services/test_savings_dashboard_service.py:1297`
    `is not None` / `:1298 isinstance(...,Decimal)` / `:1338 dti_ratio ==
    Decimal("0.0")` (Phase 7 7.F.1 #7, re-resolved live) -- the with-salary
    path has no pinned non-trivial value.
- **Phase-doc pointers:** `00_priors.md` 0.6 PA-12..PA-30 (`:821-839`),
  per-doc counts (`:841`); `07_test_gaps.md` Part 7.A LOOSE-ONLY verdicts
  and the findings-against-assumption notes (slices 2-4), 7.F.1 spot-check
  (`:3832-3854`); `docs/testing-standards.md` (the exact-assertion
  standard).
- **Open questions:** none. These are test-coverage gaps, not
  developer-decision questions; the PA "open"/"partially-remediated"
  statuses vs Phase 7's COVERED findings are reconciled as Phase-7's
  corrected state (the finding-against-assumption mechanism), not a new
  blocking Q.
- **Remediation direction:** Replace the directional/`is not None`
  assertions with exact hand-computed `Decimal` expectations and add the
  missing sad-path / boundary / status-machine / annual-reconciliation
  invariant tests for the debt-balance, HYSA, paycheck/tax,
  transfer-recurrence, chart-data, amortization, growth, and pension
  modules -- per the testing standard, no production-code change.
- **Blast radius / symptom link:** no displayed wrong dollar of its own;
  it is the missing regression depth around the calc modules -- a
  deterministic error in these areas would ship uncaught; no
  developer-reported symptom (test-gap aggregate, latent).

## LOW tier (provisional)

### LOW-01 -- Dead code: legacy `calculate_federal_tax` carries an inert divergence; its tests block deletion

- **Severity + rubric justification:** LOW. Rubric: LOW "dead code carrying
  an inert divergence (F-040)" (section 3.3 LOW, explicit). `calculate_federal_tax`
  subtracts the standard deduction but NOT `pre_tax_deduction` and returns an
  annual (not per-period) figure -- a DEFINITION divergence from the canonical
  `calculate_federal_withholding` -- but it has zero `app/` consumers
  (grep-confirmed this session), so the divergence is unreachable and produces
  no displayed wrong dollar. LOW by the rubric; the only live cost is a
  future-drift trap if a new caller wires it in, plus the test-deletion tension.
- **Category:** drift (inert).
- **Plain-language description:** There is a legacy federal-tax function that
  computes tax differently from the real engine -- it does not subtract pre-tax
  deductions and returns a full-year figure instead of the per-period
  withholding -- but nothing in the application calls it; the only references
  are its own definition and a backward-compatibility test class. So it ships
  no wrong number today. It is recorded because (a) it is a latent trap: a
  future developer who wires it in inherits the wrong taxable base silently,
  and (b) its `TestLegacyWrapper` tests pin its current output, so deleting the
  dead code turns those green tests red -- a CLAUDE.md-rule-5 tension the
  developer must see before removing it.
- **Subsumes:** Phase 3 **F-040** legacy `calculate_federal_tax` DEAD_CODE
  (zero `app/` consumers, grep-confirmed -- `03_consistency.md:3192-3228`);
  Phase 7 `federal_tax` F-040 conditional anti-coverage flag
  (`07_test_gaps.md:3926`; deleting the dead code turns the `TestLegacyWrapper`
  pins red -- recorded so a green bar is not read as coverage of a live path).
- **Governing E-NN:** NONE -> dead-code (no E-NN governs an unreachable
  function; F-040's remediation is a cleanup, recorded report-only).
- **Evidence (re-resolved to live source this session, key line quoted):**
  - Zero-consumer grep re-run this session: `grep -rn
    "calculate_federal_tax" app/` -> the single line
    `app/services/tax_calculator.py:215:def calculate_federal_tax(annual_gross,
    bracket_set):`; `grep -rn "calculate_federal_tax" app/ | grep -v "def
    calculate_federal_tax" | wc -l` -> **0** (zero non-def `app/`
    consumers, exactly as F-040 recorded).
  - The inert divergence: `app/services/tax_calculator.py:233` -- `taxable
    = Decimal(str(annual_gross)) - Decimal(str(bracket_set.standard_deduction))`
    (subtracts standard deduction only, NO `pre_tax_deduction`); `:234
    return _apply_marginal_brackets(taxable, bracket_set.brackets)` (returns
    ANNUAL, not per-period) -- the DEFINITION divergence vs canonical, inert
    because unreachable.
  - The deletion-blocking pins: `tests/test_services/test_tax_calculator.py:510`
    `class TestLegacyWrapper:`; `:518 assert result == Decimal("5700.00")`,
    `:522 assert result == Decimal("0")`, `:526 assert result ==
    Decimal("0")` -- exact pins on the legacy interface (the only references
    outside the def).
- **Phase-doc pointers:** `03_consistency.md` F-040 (`:3192-3228`), P3-d1
  verification (c) (`:3251-3255`); `07_test_gaps.md` `federal_tax` legacy
  F-040 (`:1488-1495`), 7.F.4 conditional flag (`:3926`);
  `02_concepts.md:3279` (catalog E2).
- **Open questions:** none. F-040 records "Open questions for the
  developer: none." The deletion-vs-keep-as-public-API choice is the
  remediation decision, not an audit open question.
- **Remediation direction:** Delete `calculate_federal_tax` and its
  `TestLegacyWrapper` together (or, if retained as a public API, make it
  subtract pre-tax and document why it returns annual) -- F-040's recorded
  report-only direction.
- **Blast radius / symptom link:** no displayed wrong dollar (zero `app/`
  consumers); inert -- the only risks are a future caller inheriting the
  wrong taxable base and the rule-5 test-deletion tension; no
  developer-reported symptom.

### LOW-02 -- Transfer Invariant 4 structural nuance: `transfer_recurrence.py:201` deletes a `Transfer` directly, bypassing `transfer_service.delete_transfer`

- **Severity + rubric justification:** LOW. Rubric: LOW "minor duplication
  with low blast radius." Phase 6 B6-03 proved Transfer Invariant 4 HOLDS:
  shadows are created/mutated only via `transfer_service`, the
  `recurrence_engine` guard actively refuses shadow mutation, and the one
  `transfer_recurrence.py:201` direct `db.session.delete(xfer)` does NOT
  corrupt the shadow pair because the `transaction.transfer_id` FK is
  `ondelete="CASCADE"`, so Postgres removes both shadows atomically -- exactly
  as the canonical path also relies on. The only divergence is forensic: the
  bypass skips the service's orphan self-verification and the
  `EVT_TRANSFER_HARD_DELETED` audit event. No financial drift, no displayed
  wrong dollar, balance-correctness preserved -- LOW (a latent E-08
  literal-wording boundary erosion worth recording, not a money finding).
- **Category:** SOLID (boundary).
- **Plain-language description:** Recurring-transfer regeneration deletes
  superseded auto-generated transfers with a bare ORM delete instead of going
  through the one canonical transfer-deletion service function. The shadow
  expense/income pair stays correct because the database FK cascade removes
  both shadows atomically (the same cascade the canonical path depends on), so
  no balance is wrong and the two-shadow invariant holds. What the shortcut
  loses is the canonical path's two safety steps: the post-delete
  orphan-verification self-check, and the `EVT_TRANSFER_HARD_DELETED` audit-log
  event. So a template regeneration that prunes transfers leaves no forensic
  audit trail for those deletions and skips the check that would surface a
  future FK misconfiguration. It is a boundary/maintainability nuance against
  the literal Invariant-4 wording ("all mutations go through the transfer
  service"), not a correctness defect today.
- **Subsumes:** Phase 6 **B6-03** (Transfer Invariant 4 HOLDS, with the one
  recorded `transfer_recurrence.py:201` direct-delete nuance;
  `06_dry_solid.md:1862-1970` -- a negative finding plus the single carried
  boundary observation).
- **Governing E-NN:** E-08 (no code path mutates a shadow outside the
  transfer service; the recommendation is exactly E-08's end state, no
  invented target).
- **Evidence (re-resolved to live source this session, key line quoted):**
  - The bypass: `app/services/transfer_recurrence.py:141` `def
    regenerate_for_template(template, periods, scenario_id,
    effective_from=None):`; `:200-201` `for xfer in to_delete:` /
    `db.session.delete(xfer)` then `:202 db.session.flush()` -- a bare ORM
    delete, followed only by the aggregate `:206 log_event(... 
    EVT_TRANSFER_RECURRENCE_REGENERATED ...)` (no per-transfer
    `EVT_TRANSFER_HARD_DELETED`, no orphan verify).
  - The canonical path it bypasses: `app/services/transfer_service.py:616`
    `def delete_transfer(transfer_id, user_id, soft=False):`; `:661
    db.session.delete(xfer)` then the orphan self-check and `:679
    log_event(... EVT_TRANSFER_HARD_DELETED ...)` (the forensic event the
    bypass skips). Invariant preserved: `transaction.transfer_id` carries
    `ondelete="CASCADE"` so both shadows drop atomically on either path.
- **Phase-doc pointers:** `06_dry_solid.md` B6-03 (`:1862-1970`), Part 6.3
  (`:1972+`); `CLAUDE.md:139` (Invariant 4 literal wording); `00_priors.md`
  E-08 (`:344-346`).
- **Open questions:** none. B6-03 is an independent re-proof; the
  CASCADE-preserves-the-pair fact is established, the nuance is the
  forensic/literal-wording erosion only.
- **Remediation direction:** Route the `regenerate_for_template` deletion
  loop through `transfer_service.delete_transfer(xfer.id, ..., soft=False)`
  so the single canonical hard-delete path (orphan self-verify +
  `EVT_TRANSFER_HARD_DELETED` audit) is the only writer-path into
  `budget.transfers` deletions -- the E-08 end state.
- **Blast radius / symptom link:** no financial drift (CASCADE keeps the
  shadow pair atomic and consistent); latent forensic-only risk -- a
  template regeneration's transfer deletions leave no audit trail and skip
  the orphan self-check; no developer-reported symptom (boundary nuance,
  not previously observed in Phases 3-5).

### LOW-03 -- Latent, no current wrong number: missing carry-forward scenario filter; Option-A CD columns never added

- **Severity + rubric justification:** LOW. Rubric: LOW "minor ... with low
  blast radius" / latent missing feature with no wrong number. W-019 (Option-A
  CD columns) is a never-added feature, not a defect in shipped math -- no
  number is wrong because the columns simply do not exist. PA-08 (the
  carry-forward scenario_id filter) is re-resolved this session as
  **remediated** in live source (`carry_forward_service.py:262` now filters
  `Transaction.scenario_id == scenario_id`), so its only-if-scenarios-enabled
  risk no longer exists -- a prior-status drift surfaced, not smoothed (R-10,
  new this session, the R-8/R-9 precedent). Neither member produces a
  displayed wrong dollar; LOW.
- **Category:** drift (latent).
- **Plain-language description:** Two unrelated latent items with no current
  wrong number. (1) The prior audit flagged that carry-forward filtered
  projected transactions by period and status but not scenario, so enabling
  scenarios could move transactions across all scenarios; re-checked at live
  source this session, carry-forward now filters by `scenario_id` in the
  context-building query (and the mutating bulk UPDATE operates only on those
  IDs), so this specific defect is fixed -- the prior "open" status is stale
  (recorded as a reconciliation item, not resolved here). (2) The
  account-parameter plan's Option-A CD support (nullable `maturity_date` /
  `term_months` on the interest-params model) was planned but never built; the
  model has only `apy` and `compounding_frequency`. That is a missing feature,
  not a miscalculation -- nothing is displayed wrong, there is simply no CD
  term to display.
- **Subsumes:** Prior-audit **PA-08** (`00_priors.md:817`;
  `carry_forward_service` missing `scenario_id` filter -- now remediated,
  R-10); Phase 3 cmp-1 **W-019** VIOLATED (Option-A CD columns never added;
  latent missing feature, no wrong number; `03_consistency.md:6038`,
  cmp-1 verdict re-read this session).
- **Governing E-NN:** NONE -> latent-no-current-wrong-number (no E-NN
  governs an unbuilt feature or the now-remediated scenario filter).
- **Evidence (re-resolved to live source this session, key line quoted):**
  - W-019 (still VIOLATED): `app/models/interest_params.py` -- the column
    set is `id` (`:40`), `account_id` (`:41`), `apy` (`:60`),
    `compounding_frequency` (`:61`); `grep -n "maturity_date\|term_months"
    app/models/interest_params.py` -> **no match**. The Option-A CD
    columns were never added; latent missing feature, no wrong number.
  - **PA-08 prior-status drift (surfaced, not smoothed -- R-10, new this
    session):** PA-08 (`00_priors.md:817`, status `open`) states
    `carry_forward_service` "filters projected transactions by
    `pay_period_id` and `status_id` but not `scenario_id`". Live source:
    `app/services/carry_forward_service.py:261-263` --
    `Transaction.pay_period_id == source_period_id,` /
    `Transaction.scenario_id == scenario_id,` / `Transaction.status_id ==
    projected_id,` (the SELECT that builds `ctx.discrete_txns`); the
    mutating bulk UPDATE (`:405-412`) operates on `Transaction.id.in_(
    template_ids)` drawn from that scenario-filtered set; the function
    signature takes `scenario_id` and the docstring `:318` states it
    "Prevents [cross-scenario mixing]". The PA-08 `open` premise is stale
    w.r.t. live source -- the scenario filter is present at `:262`. PA-08
    **remains mapped `-> LOW-03`** in Part 8.B (the surjection is
    unchanged; W-019 is the cluster's still-latent substance); only PA-08's
    remediation status is the discrepancy. Recorded as reconciliation item
    R-10, NOT resolved here (P8-e owns the mechanical reconciliation
    against `00_priors.md`'s PA-08 row, the R-8/R-9 precedent).
- **Phase-doc pointers:** `03_consistency.md` cmp-1 W-019
  (`:6038`/`:4435`/`:4957`); `00_priors.md` PA-08 (`:817`); the
  `account_param_arch` plan Option A (W-019's source plan).
- **Open questions:** none blocking. PA-08 is a prior-audit hardening item
  now remediated (R-10, a documentation-correction against `00_priors.md`,
  surfaced not resolved); W-019 is a planned-not-built feature decision for
  the developer (whether to add CD support), not an audit open question.
- **Remediation direction:** Update `00_priors.md`'s PA-08 row to the
  remediated state (R-10), and -- only if CD support is desired -- add the
  Option-A nullable `maturity_date` / `term_months` columns to the
  interest-params model (W-019); neither is a wrong-number fix.
- **Blast radius / symptom link:** no displayed wrong dollar -- PA-08's
  cross-scenario risk is remediated and gated on a disabled feature
  regardless; W-019 is an absent feature (no CD term shown because none is
  modeled); no developer-reported symptom (latent).

### LOW-04 -- Phase-4 minor classification nit

- **Severity + rubric justification:** LOW. Rubric: LOW "the minor Phase-4
  classification nit" (section 3.3 LOW, explicit). `budget.escrow_components.inflation_rate`
  is a real stored column, AUTHORITATIVE by construction (a user-entered
  escrow-inflation input with a DB CHECK, no second producer), covered in
  Phase-4 prose but never given its own line in the D3 consolidated
  classification table. The defect is purely a Phase-4 documentation
  completeness gap: the column is correctly authoritative, no drift surface, no
  wrong number -- LOW.
- **Category:** source-of-truth (documentation).
- **Plain-language description:** This is a bookkeeping gap in the Phase-4
  source-of-truth audit, not a code defect. The escrow-component inflation-rate
  column is a straightforward user-entered input with a database range check
  and a single producer; Phase 4 discussed it in the escrow prose but did not
  add it as its own row in the consolidated stored-column classification table,
  so a reader scanning that table alone would not see it listed. The column
  itself is correct and authoritative; only the audit table is one row short.
- **Subsumes:** `budget.escrow_components.inflation_rate` covered at
  `04_source_of_truth.md:1307-1308` but with no standalone D3 classification
  line (the D2 nit; `04:2043,2109` -- AUTHORITATIVE implicit). Cross-ref
  R-5 (the Family-D triage closing-completeness correction class).
- **Governing E-NN:** NONE -> classification-nit (a documentation
  correction against Phase 4, no E-NN, no code change).
- **Evidence (re-resolved to live source this session, key line quoted):**
  - The column is live and authoritative-by-construction:
    `app/models/loan_features.py:127` -- `inflation_rate = db.Column(
    db.Numeric(5, 4), nullable=True)` (the `EscrowComponent` model;
    `budget.escrow_components`); CHECK `:112-114` `"inflation_rate IS NULL
    OR (inflation_rate >= 0 AND inflation_rate <= 1)"` name
    `ck_escrow_components_valid_inflation_rate`; comment `:107` `# F-077 /
    C-24: ``inflation_rate`` is nullable (NULL = no ...` -- a single-source
    user input with a DB-enforced domain, no second producer, no drift
    surface. The nit is the missing Phase-4 D3 row, not anything in the
    code.
- **Phase-doc pointers:** `04_source_of_truth.md` escrow coverage
  (`:1307-1308`), D2/D3 tables (`:2043`, `:2109`), Family-D triage closing
  claim (`:1885-1886`, R-5).
- **Open questions:** none. The column's role is settled
  (AUTHORITATIVE-implicit); the only action is a Phase-4 documentation
  correction.
- **Remediation direction:** Add the missing standalone
  `budget.escrow_components.inflation_rate` -> AUTHORITATIVE row to the
  Phase-4 D3 classification table so the table is complete (documentation
  only).
- **Blast radius / symptom link:** none -- no code, no displayed figure,
  no developer-reported symptom; a Phase-4 audit-table completeness
  correction.

### LOW-05 -- Documentation-correction: `estimated_retirement_tax_rate` model comment contradicts the code's NULL semantics (carried tail)

- **Severity + rubric justification:** LOW. Rubric: LOW "formatting, naming
  ... low blast radius" -- a model-comment-vs-code divergence. The
  `estimated_retirement_tax_rate` column's source-of-truth role is CLOSED
  AUTHORITATIVE (Phase-4 P4-f); the only finding is that the model comment
  promises a bracket-based fallback when the value is NULL while the code
  applies NO tax adjustment when it is None. A-26 already decided the
  remediation direction (correct the comment; do NOT add a bracket fallback),
  so this is a one-line documentation correction with no money impact -- LOW.
  The contract itself (whether the promised fallback should instead be built)
  is the Phase-9 carried tail, NOT decided here (contract item 4 / G8).
- **Category:** source-of-truth (documentation), definition.
- **Plain-language description:** The user-settings model has an
  estimated-retirement-tax-rate column whose code comment says that when it is
  left unset (NULL) the app will "fall back to a current bracket-based
  estimate." The code does not do that: when the value is None the
  retirement-gap calculator simply applies no tax adjustment at all (the
  `if estimated_tax_rate is not None:` guards skip the tax math entirely). So
  the documented contract and the actual behavior disagree. The column itself
  is authoritative and there is no second producer; the practical effect is
  only that a developer reading the comment would expect a bracket fallback
  that does not exist. A-26 resolved the direction: fix the comment to say
  "NULL = no retirement-tax adjustment applied"; do not build the fallback.
  Whether the product SHOULD apply a bracket fallback is the open contract
  carried unchanged to Phase 9.
- **Subsumes:** **F-046-SoT** (Phase-4 coverage GAP -> role CLOSED ->
  AUTHORITATIVE, P4-f; `09_open_questions.md:1726-1744`); the **Q-26 sub-2**
  carried tail (`09_open_questions.md` Q-26 sub-2; A-26 decided the
  documentation direction, the adjudication contract carried to Phase 9
  unchanged); the secondary truthiness guard
  `retirement_dashboard_service.py:224` (`and settings.estimated_retirement_tax_rate`
  -- 0-vs-None) is routed to the F-042 family (CRIT-04), recorded not
  re-litigated here. Cross-ref R-5 (the Family-D closing-completeness
  correction that omitted this column).
- **Governing E-NN:** NONE -> documentation-correction; A-26 decided
  (correct the model comment to "NULL = no retirement-tax adjustment
  applied"; do NOT add a bracket-based fallback). The model-vs-code contract
  decision is carried to Phase 9 unchanged (the only Phase-8 carried-open
  tail).
- **Evidence (re-resolved to live source this session, key line quoted):**
  - The model comment (promises a fallback): `app/models/user.py:212-215`
    -- `# F-077 / C-24: Estimated effective tax rate during` / `#
    retirement (NULL = unset, fall back to current bracket-` / `# based
    estimate).  Same percent-to-decimal convention as` /
    `# ``safe_withdrawal_rate``.`; the column `:242
    estimated_retirement_tax_rate = db.Column(db.Numeric(5, 4),
    nullable=True)` (CLOSED AUTHORITATIVE, NOT NULL-able by design).
    **Minor citation note (not a verdict change):** the Phase-6/Q-26
    handoff cited the comment at `app/models/user.py:215-216`; live it is
    `:212-215` (the model shifted) -- behaviour exactly as described, the
    CRIT-01 calendar-citation-note precedent.
  - The code (applies no tax, no fallback): `app/services/retirement_dashboard_service.py:223-224`
    -- `Decimal(str(settings.estimated_retirement_tax_rate))` / `if
    settings and settings.estimated_retirement_tax_rate` (truthiness
    guard); `:234 estimated_tax_rate=tax_rate` passed to
    `app/services/retirement_gap_calculator.py:37 def calculate_gap(`
    where `:76 if estimated_tax_rate is not None:` / `:79
    monthly_pension_income * (1 - estimated_tax_rate)` and `:110 if
    estimated_tax_rate is not None:` -- when None, the `(1 - rate)`
    adjustment is SKIPPED entirely (no bracket-based fallback computed),
    contradicting the model comment.
- **Phase-doc pointers:** `09_open_questions.md` Q-26 / A-26 (the
  carried-tail contract); `04_source_of_truth.md` F-046-SoT P4-f closure
  (`:1726-1744`); `06_dry_solid.md` A-26 tail carried (`:2218-2225`);
  CRIT-04 (the F-042 family that owns the `:224` 0-vs-None truthiness
  facet).
- **Open questions:** **Q-26 sub-2 (carried to Phase 9 unchanged --
  contract item 4 / G8).** A-26 decided the documentation-correction
  direction (fix the comment, do not add a fallback), so LOW-05 records
  the comment-vs-code divergence as the finding; the developer-adjudication
  contract -- whether a bracket-based fallback SHOULD exist -- is the only
  Phase-8 carried-open tail and is passed to Phase 9 exactly as Phases
  5/6/7 carried it (G8), NOT resolved here.
- **Remediation direction:** Correct the `app/models/user.py:212-215`
  comment to "NULL = no retirement-tax adjustment applied" so the
  documented contract matches the code (A-26's decided direction; the
  build-the-fallback question stays open for Phase 9).
- **Blast radius / symptom link:** no displayed wrong dollar from the
  comment itself (the `:224` 0-vs-None money facet is CRIT-04's, cross-
  referenced not double-counted); the risk is a developer trusting the
  documented bracket fallback that does not exist; no developer-reported
  symptom (documentation-correction; the carried Phase-9 tail).

---

# Part 8.B -- master reverse-index (surjection proof)

Every section-1 source ID, by source register, with its prior-phase verdict
and its single disposition. `NOT-A-FINDING` reasons are the closed set;
`POINTER` is the section-1 row-14 disposition for PT-NN.

## B.1 Phase 3 consistency findings (`03_consistency.md` F-001..F-056)

| Source ID | Prior-phase verdict (C1 `:5984-6047`) | Disposition |
| --- | --- | --- |
| F-001 account_balance | DIVERGE; SILENT+SCOPE+SOURCE+PLAN | -> CRIT-01 |
| F-002 checking_balance | DIVERGE; SILENT | -> CRIT-01 |
| F-003 projected_end_balance | DIVERGE; SILENT+SOURCE | -> CRIT-01 |
| F-004 period_subtotal | UNKNOWN; Q-10 | -> CRIT-01 |
| F-005 chart_balance_series | DIVERGE; SILENT | -> CRIT-01 |
| F-006 net_worth | UNKNOWN; Q-15 | -> CRIT-01 |
| F-007 savings_total | UNKNOWN; Q-15 | -> CRIT-01 |
| F-008 debt_total | UNKNOWN; Q-15 (internal DIVERGE holds) | -> CRIT-02 |
| F-009 proj_end vs checking (sym #1) | DIVERGE; SILENT | -> CRIT-01 |
| F-010 _sum_remaining vs _sum_all | AGREE | NOT-A-FINDING: AGREE (the structural duplication is D6-06 -> CRIT-01; F-010's numeric verdict is AGREE) |
| F-011 credit-status handling | AGREE | NOT-A-FINDING: AGREE |
| F-012 shadow-transaction handling (Inv 5) | AGREE | NOT-A-FINDING: AGREE |
| F-013 monthly_payment (sym #2) | DIVERGE; SILENT+DEFINITION+PLAN | -> CRIT-02 |
| F-014 loan_principal_real (sym #3) | DIVERGE; SOURCE+SCOPE+SILENT | -> CRIT-02 |
| F-015 loan_principal_stored | DIVERGE; SOURCE | -> CRIT-02 |
| F-016 loan_principal_displayed | UNKNOWN; Q-11 | -> CRIT-02 |
| F-017 principal_paid_per_period | DIVERGE; SCOPE | -> HIGH-08 |
| F-018 interest_paid_per_period | DIVERGE; DEFINITION | -> HIGH-08 |
| F-019 escrow_per_period | AGREE | NOT-A-FINDING: AGREE |
| F-020 total_interest | DIVERGE; DEFINITION (by design) | -> HIGH-08 |
| F-021 interest_saved | DIVERGE; ROUNDING | -> HIGH-08 |
| F-022 months_saved | DIVERGE; DEFINITION | -> HIGH-08 |
| F-023 payoff_date | DIVERGE; SILENT | -> HIGH-08 |
| F-024 loan_remaining_months internal verify | AGREE | NOT-A-FINDING: AGREE |
| F-025 dti_ratio | AGREE | NOT-A-FINDING: AGREE (Phase-7 LOOSE-ONLY test gap -> MED-07) |
| F-026 5/5 ARM stability (sym #4) | DIVERGE; SILENT+PLAN | -> CRIT-02 |
| F-027 effective_amount (~43-site bypass) | DIVERGE; SILENT (+E-16) | -> CRIT-01 (E-16 facet cross-ref MED-04; Q-08 facet cross-ref MED-03) |
| F-028 entry-progress cross-anchor | UNKNOWN; Q-08 | -> MED-03 |
| F-029 transfer_amount | AGREE | NOT-A-FINDING: AGREE |
| F-030 transfer_amount_computed | AGREE | NOT-A-FINDING: AGREE |
| F-031 shadow / budget.transfers reads (Inv 5) | AGREE | NOT-A-FINDING: AGREE |
| F-032 paycheck_gross off-engine dti | DIVERGE; DEFINITION | -> MED-06 |
| F-033 paycheck_net | AGREE | NOT-A-FINDING: AGREE |
| F-034 taxable_income | AGREE | NOT-A-FINDING: AGREE |
| F-035 federal_tax (bracket vs calibrated, gated) | AGREE | NOT-A-FINDING: AGREE |
| F-036 state_tax | AGREE | NOT-A-FINDING: AGREE |
| F-037 fica SS-cap bypass | DIVERGE; DEFINITION | -> CRIT-03 |
| F-038 pre_tax_deduction (ordering + Q-13 base) | AGREE | NOT-A-FINDING: AGREE (Q-13 resolved by E-20 -> HIGH-03 owns the calibration base) |
| F-039 post_tax_deduction (ordering) | AGREE | NOT-A-FINDING: AGREE |
| F-040 legacy calculate_federal_tax | DEAD_CODE | -> LOW-01 |
| F-041 apy_interest (single engine; PA-06 body) | AGREE | NOT-A-FINDING: AGREE (PA-06 cross-producer body -> MED-05) |
| F-042 growth | DIVERGE; SILENT | -> CRIT-04 |
| F-043 employer_contribution | DIVERGE; SILENT | -> HIGH-07 |
| F-044 contribution_limit_remaining | AGREE (single-path) | NOT-A-FINDING: AGREE -- **F-044 miscite, reconciliation item R-2** |
| F-045 ytd_contributions | AGREE | NOT-A-FINDING: AGREE |
| F-046 goal_progress (GP1/GP2) | AGREE | NOT-A-FINDING: AGREE (GP2 conditional anti-coverage Q-08 -> MED-03) |
| F-047 emergency_fund_coverage_months | AGREE | NOT-A-FINDING: AGREE |
| F-048 cash_runway_days | AGREE | NOT-A-FINDING: AGREE |
| F-049 pension_benefit_annual | AGREE | NOT-A-FINDING: AGREE |
| F-050 pension_benefit_monthly | AGREE | NOT-A-FINDING: AGREE |
| F-051 year_summary_jan1_balance | UNKNOWN; Q-15 | -> CRIT-01 |
| F-052 year_summary_dec31_balance | UNKNOWN; Q-15 | -> CRIT-01 (balance-as-of-date facet cross-ref HIGH-02) |
| F-053 year_summary_principal_paid | AGREE-by-construction | NOT-A-FINDING: AGREE |
| F-054 year_summary_growth | YG1 UNKNOWN Q-15 / YG2 AGREE-by-construction | YG1 -> CRIT-01; YG2 NOT-A-FINDING: AGREE (split row) |
| F-055 year_summary_employer_total | DIVERGE; SILENT+PLAN | -> HIGH-07 |
| F-056 entry_sum_total + entry_remaining | entry_sum_total AGREE / entry_remaining UNKNOWN-Q-08 | entry_remaining -> MED-03; entry_sum_total NOT-A-FINDING: AGREE (split row) |

DIVERGE coverage check: all 21 C1 DIVERGE IDs (F-001, F-002, F-003, F-005,
F-009, F-013, F-014, F-015, F-017, F-018, F-020, F-021, F-022, F-023, F-026,
**F-027**, F-032, F-037, F-042, F-043, F-055) carry a `-> cluster`. All 9
UNKNOWN + the F-056 sub carry a `-> cluster`. (Reconciliation item R-1: Phase
7's 7.F.3 builds a 20-DIVERGE canonical set that OMITS F-027 and attributes
the 21st to an F-025 substring; Phase 3's C1 register is authoritative and
classes F-027 DIVERGE. F-027 is mapped to a cluster regardless.)

## B.2 Phase 3 C2 symptom map (`03_consistency.md:6049-6060`) and Phase 5 roots

| Source ID | Prior-phase root | Disposition |
| --- | --- | --- |
| Symptom #1 ($160 grid vs $114.29 /savings) | F-009 + W-277 | -> CRIT-01 |
| Symptom #2 (mortgage 1911/1914/1912 -> 1910.95) | F-013 on F-026 | -> CRIT-02 |
| Symptom #3 (current principal does not move on settle) | F-014 | -> CRIT-02 |
| Symptom #4 (5/5 ARM payment creep) | F-026 | -> CRIT-02 |
| Symptom #5 (/accounts matches nowhere) | F-001 + F-008 | -> CRIT-01 (loan-base facet cross-ref CRIT-02) |

## B.3 Phase 3 C3 CRITICAL pre-list (`03_consistency.md:6062-6075`) -- severity INPUT only, re-tested per cluster

| C3 item | Disposition |
| --- | --- |
| F-037 fica SS-cap bypass | -> CRIT-03 (severity re-derived P8-b, not copied) |
| F-042 growth phantom income | -> CRIT-04 (severity re-derived P8-b) |
| Q-19 / W-262 RECEIVED hard-delete | -> CRIT-05 (Refinement A) |
| W-065 / W-277 calendar drift | -> HIGH-02 (**R-3**: C3 pre-listed CRITICAL; phase8_plan P8-b scopes 5 CRITICALs and routes calendar to P8-c/D6-04; P8-e re-derives the tier from the rubric) |
| (symptom CRITICALs) F-001/F-008/F-009/F-013/F-014/F-026 | -> CRIT-01 / CRIT-02 (already mapped B.1/B.2) |

## B.4 Phase 3 W-NN non-HOLDS corpus (cmp-1..5, `03_consistency.md:6034-6047`)

| Source ID | Prior-phase verdict | Disposition |
| --- | --- | --- |
| W-019 (cmp-1) | VIOLATED (latent missing feature, no wrong number) | -> LOW-03 |
| W-010 (cmp-1) | PARTIALLY_HOLDS (PLAN_DRIFT-STRUCTURAL) | NOT-A-FINDING: HOLDS (structural, superseded by metadata-flag design) |
| W-013 (cmp-1) | PARTIALLY_HOLDS (PLAN_DRIFT-STRUCTURAL) | NOT-A-FINDING: HOLDS |
| W-014 (cmp-1) | PARTIALLY_HOLDS (PLAN_DRIFT-STRUCTURAL) | NOT-A-FINDING: HOLDS |
| W-016 (cmp-1) | PARTIALLY_HOLDS (PLAN_DRIFT-STRUCTURAL) | NOT-A-FINDING: HOLDS |
| W-018 (cmp-1) | PARTIALLY_HOLDS (PLAN_DRIFT-STRUCTURAL) | NOT-A-FINDING: HOLDS |
| W-021 (cmp-1) | PARTIALLY_HOLDS (PLAN_DRIFT-STRUCTURAL) | NOT-A-FINDING: HOLDS |
| W-022 (cmp-1) | PARTIALLY_HOLDS (PLAN_DRIFT-STRUCTURAL) | NOT-A-FINDING: HOLDS |
| W-030 (cmp-1) | PARTIALLY_HOLDS (PLAN_DRIFT-STRUCTURAL) | NOT-A-FINDING: HOLDS |
| W-040 (cmp-1) | PARTIALLY_HOLDS (PLAN_DRIFT-STRUCTURAL) | NOT-A-FINDING: HOLDS |
| W-020 (cmp-1) | N/A-OPTION-NOT-ADOPTED | NOT-A-FINDING: superseded-by-A-NN (Option A not adopted; A-04/E-18 family) |
| W-126 (cmp-2) | PARTIALLY_HOLDS (HTTP-status only, no dollar) | NOT-A-FINDING: HOLDS (no money figure) |
| W-277 (cmp-3) | UNKNOWN-Q-18 + escalated entries-load + SCOPE | -> HIGH-02 |
| W-262 (cmp-3) | HOLDS literal + escalated RECEIVED hard-delete (Q-19) | -> CRIT-05 |
| W-065 (cmp-3) | HOLDS + escalated filter-set DEFINITION_DRIFT (F-004) | -> HIGH-02 |
| W-082 (cmp-3) | N/A-TEST-CLAIM | NOT-A-FINDING: superseded-by-A-NN (test-claim N/A) |
| W-083 (cmp-3) | N/A-TEST-CLAIM | NOT-A-FINDING: superseded-by-A-NN (test-claim N/A) |
| W-084 (cmp-3) | N/A-TEST-CLAIM | NOT-A-FINDING: superseded-by-A-NN (test-claim N/A) |
| cmp-4 corpus | 0 non-HOLDS (20/20 HOLDS, money-math hand-verified) | NOT-A-FINDING: HOLDS |
| cmp-5 corpus | 0 non-HOLDS (11 IN-FINANCIAL HOLDS, 17 OUT-OF-SCOPE) | NOT-A-FINDING: HOLDS |

## B.5 Phase 4 stored-column findings (`04_source_of_truth.md:2099-2127` D3)

| Source ID (column) | Phase-4 class | Disposition |
| --- | --- | --- |
| budget.accounts.current_anchor_balance | AUTHORITATIVE | -> CRIT-01 (subsumed; AUTHORITATIVE anchor input, the cluster's anchor substrate) |
| budget.accounts.current_anchor_period_id | UNCLEAR (Q-20) | -> CRIT-01 |
| budget.account_anchor_history.anchor_balance | CACHED | -> CRIT-01 |
| budget.loan_params.current_principal | UNCLEAR (Q-22) | -> CRIT-02 |
| budget.loan_params.original_principal | AUTHORITATIVE | NOT-A-FINDING: AUTHORITATIVE |
| budget.loan_params.interest_rate | UNCLEAR (Q-23) | -> CRIT-02 |
| budget.rate_history.interest_rate | AUTHORITATIVE | NOT-A-FINDING: AUTHORITATIVE |
| budget.escrow_components.annual_amount | AUTHORITATIVE | NOT-A-FINDING: AUTHORITATIVE |
| budget.escrow_components.inflation_rate | AUTHORITATIVE (implicit; D2 nit) | -> LOW-04 |
| budget.interest_params.apy | AUTHORITATIVE (silent-default hazard Q-24 #2) | -> HIGH-06 |
| budget.investment_params.assumed_annual_return | AUTHORITATIVE (float default; 0-vs-None Q-24 #2 / F-042) | -> HIGH-06 (float-default/E-28 facet; 0-vs-None facet cross-ref CRIT-04) |
| budget.investment_params.annual_contribution_limit | AUTHORITATIVE (tri-consumer 0/None Q-24 #3) | -> HIGH-06 |
| budget.investment_params.employer_flat_percentage | AUTHORITATIVE (0/None benign) | NOT-A-FINDING: AUTHORITATIVE |
| budget.investment_params.employer_match_percentage | AUTHORITATIVE (0/None benign) | NOT-A-FINDING: AUTHORITATIVE |
| budget.investment_params.employer_match_cap_percentage | AUTHORITATIVE (0/None benign) | NOT-A-FINDING: AUTHORITATIVE |
| budget.transactions.actual_amount | AUTHORITATIVE (effective_amount bypass = F-027/F-028) | NOT-A-FINDING: AUTHORITATIVE (the bypass surface is F-027 -> CRIT-01 / F-028 -> MED-03) |
| budget.transactions.estimated_amount | AUTHORITATIVE / DERIVED-by-invariant (shadows) | NOT-A-FINDING: AUTHORITATIVE (shadow invariant = F-029/F-031, AGREE) |
| budget.savings_goals.contribution_per_period | AUTHORITATIVE | NOT-A-FINDING: AUTHORITATIVE |
| salary.calibration_overrides.effective_federal_rate | UNCLEAR (Q-25) | -> HIGH-03 |
| salary.calibration_overrides.effective_state_rate | UNCLEAR (Q-25) | -> HIGH-03 |
| salary.calibration_overrides.effective_ss_rate | UNCLEAR (Q-25) | -> HIGH-03 |
| salary.calibration_overrides.effective_medicare_rate | UNCLEAR (Q-25) | -> HIGH-03 |
| auth.user_settings.estimated_retirement_tax_rate | GAP -> UNCLEAR (Q-26); role CLOSED AUTHORITATIVE (P4-f) | -> LOW-05 |
| Family D triage block (~38 cols, `04:1841-1883`) | AUTHORITATIVE (per-column grep-backed) | NOT-A-FINDING: AUTHORITATIVE (no drift surface; F-046-SoT closing-claim correction -> R-5) |

UNCLEAR coverage check: all 7 UNCLEAR/GAP columns
(`current_anchor_period_id`, `current_principal`, `interest_rate`, the four
`effective_*_rate`, and the `estimated_retirement_tax_rate` GAP) carry a
`-> cluster` (CRIT-01, CRIT-02 x2, HIGH-03 x4, LOW-05); none `NOT-A-FINDING`.

## B.6 Phase 4 drift register (`04_source_of_truth.md:2129-2146`)

| Source ID | Disposition |
| --- | --- |
| Drift register #1 (checking $160 vs $114.29) | -> CRIT-01 (= symptom #1; reconciled against C2 / Phase 5, G6) |
| Drift register #2 (mortgage 1911 -> 1910.95) | -> CRIT-02 (= symptom #2) |
| Drift register #3 (current_principal not updating) | -> CRIT-02 (= symptom #3) |
| Drift register #4 (ARM payment drift) | -> CRIT-02 (= symptom #4) |
| Drift register #5 (/accounts matches nothing) | -> CRIT-01 (= symptom #5) |

## B.7 Phase 6 structural findings (`06_dry_solid.md`)

| Source ID | Governing E-NN (Phase 6) | Disposition |
| --- | --- | --- |
| D6-01 no single loan resolver | E-18 | -> CRIT-02 |
| D6-02 no single anchor resolver | E-19 | -> CRIT-01 |
| D6-03 no single period-subtotal producer | E-25 | -> CRIT-01 |
| D6-04 no single balance-as-of-date path | E-27 | -> HIGH-02 |
| D6-05 no single loan-obligation aggregator; 26/12 x4 | E-24 | -> HIGH-05 |
| D6-06 `_sum_remaining`/`_sum_all` identical bodies | E-25 family | -> CRIT-01 |
| D6-07 no centralized money-rounding helper | E-26 | -> HIGH-04 |
| D6-08 hand-rolled `effective_amount` mirror 5+ sites | E-25 family | -> CRIT-01 |
| D6-09 inline status predicate across files | E-15 family | -> MED-02 |
| D6-10 4% SWR / 7% return magic literal x2 conventions | PA-05 | -> CRIT-04 |
| S6-01 investment.py route monolith | NONE -> structural-only | -> MED-01 |
| S6-02 generate_schedule length-only (negative) | NONE -> structural-only | NOT-A-FINDING: HOLDS (length-only, below the SRP bar) |
| S6-03 two per-account-type dispatchers | NONE -> structural-only | -> MED-01 |
| S6-04 `_DEDUCTION_PATH_TYPES` hardcoded enum | NONE -> structural-only | -> MED-01 |
| S6-05 residual type-identity lookup | NONE -> structural-only | -> MED-01 |
| S6-06 11-key ctx / 4-key base_args ISP | NONE -> structural-only | -> MED-01 |
| S6-07 get_loan_projection DIP duck-typing | NONE -> structural-only | -> MED-01 |
| B6-01 Routes->Services layering (negative) | NONE -> structural-only | NOT-A-FINDING: HOLDS |
| B6-02 balance calc never reads budget.transfers (negative) | E-09 | NOT-A-FINDING: HOLDS |
| B6-03 Transfer Invariant 4 HOLDS, one nuance | E-08 | -> LOW-02 |

## B.8 Phase 6 carried items (`06_dry_solid.md` handoff `:2191-2268`)

| Source ID | Disposition |
| --- | --- |
| Carried E-16 standards site `loan/_escrow_list.html:37` | -> MED-04 |
| Findings-against-prior: stale 470-line `savings.py:dashboard` tag | NOT-A-FINDING: superseded-by-A-NN (Phase-6 G4 proved it is now 4 lines; carried as the corrected state -- **R-4**) |
| Findings-against-prior: W- label-vs-source set | NOT-A-FINDING: superseded-by-A-NN (carried as the corrected state, G6; the W-NN themselves mapped in B.4) |
| Findings-against-prior: D6-01/02/05 carry-undercounts | NOT-A-FINDING: superseded-by-A-NN (carried as the corrected counts inside CRIT-02/CRIT-01/HIGH-05) |

## B.9 Phase 7 non-COVERED concepts (Part 7.A) and divergence-catching gaps (Part 7.B)

| Source ID | Phase-7 verdict | Disposition |
| --- | --- | --- |
| `account_balance` | BLOCKED-ON-OPEN-QUESTION (Q-11/Q-15) | -> CRIT-01 |
| `projected_end_balance` | BLOCKED-ON-OPEN-QUESTION (Q-11/Q-15) | -> CRIT-01 |
| `checking_balance` | (F-002 catching-gap) | -> CRIT-01 |
| `chart_balance_series` | (F-005 catching-gap) | -> CRIT-01 |
| `period_subtotal` | PRODUCER-UNKNOWN-CANNOT-PIN (Q-10) | -> CRIT-01 |
| `net_worth` | BLOCKED-ON-OPEN-QUESTION + LOOSE-ONLY (Q-15) | -> CRIT-01 |
| `savings_total` | BLOCKED-ON-OPEN-QUESTION + LOOSE-ONLY (Q-15) | -> CRIT-01 |
| `year_summary_jan1_balance` | BLOCKED-ON-OPEN-QUESTION (Q-15) | -> CRIT-01 |
| `year_summary_dec31_balance` | BLOCKED-ON-OPEN-QUESTION (Q-15) | -> CRIT-01 |
| `year_summary_growth` | BLOCKED-ON-OPEN-QUESTION (Q-15) | -> CRIT-01 |
| `debt_total` | BLOCKED-ON-OPEN-QUESTION + dual-base anti-coverage (Q-15) | -> CRIT-02 |
| `loan_principal_real` | NO-PINNED-TEST (sym #3) | -> CRIT-02 |
| `loan_principal_displayed` | PRODUCER-UNKNOWN-CANNOT-PIN (Q-11) | -> CRIT-02 |
| `monthly_payment` | NO-PINNED-TEST cross-site / ARM-window (F-013/F-026) | -> CRIT-02 |
| `dti_ratio` | LOOSE-ONLY (F-025 AGREE) | -> MED-07 |
| `taxable_income` Q-13 calibrate_preview sub | BLOCKED-ON-OPEN-QUESTION (Q-13) | -> HIGH-03 |
| `pre_tax_deduction` Q-13 pct-base sub | BLOCKED-ON-OPEN-QUESTION (Q-13) | -> HIGH-03 |
| `fica` calibration-path | COVERED bracket / calibration UNTESTED | -> CRIT-03 |
| `growth` slider/gap | COVERED engine / LOOSE-ONLY | -> CRIT-04 |
| `employer_contribution` | non-COVERED | -> HIGH-07 |
| `year_summary_employer_total` | non-COVERED | -> HIGH-07 |
| `paycheck_gross` | non-COVERED | -> MED-06 |
| `entry_remaining` | BLOCKED-ON-OPEN-QUESTION (Q-08) | -> MED-03 |
| Cross-page balance-equality meta-gap | NO catching test (3 greps 0 matches) | -> HIGH-01 |
| 20-DIVERGE no-catching-test set (Part 7.B) | NO (each) | per-finding folds into the F-NN cluster (B.1); the meta-gap is HIGH-01 |
| Phase-6 D6/S6/B6 equivalence implications (Part 7.B table) | catching-test search | fold into each D6/S6/B6 cluster (B.7) |
| All COVERED Part 7.A concepts (the other 28 of 47) | COVERED | NOT-A-FINDING: COVERED |

## B.10 Phase 7 conditional anti-coverage flags (Part 7.F.4)

| Source ID | Disposition |
| --- | --- |
| `debt_total` dual-base (Q-15) | -> CRIT-02 (recorded so a green bar is not laundered as coverage) |
| `goal_progress` GP2 (Q-08) | -> MED-03 |
| `federal_tax` legacy F-040 | -> LOW-01 |

## B.11 Phase 7 proposed tests (Part 7.C; PT-09 omitted) -- POINTER disposition

| Source ID | POINTER |
| --- | --- |
| PT-01 cross-page balance equality | POINTER -> HIGH-01 (and CRIT-01 symptoms #1/#5) |
| PT-02 loan-balance 3-way (deferred) | POINTER -> CRIT-02 |
| PT-03 anchor-None single behavior (deferred) | POINTER -> CRIT-01 |
| PT-04 same-page balance-delta == subtotal | POINTER -> CRIT-01 |
| PT-05 dual per-account dispatcher equivalence | POINTER -> MED-01 (S6-03) / CRIT-01 |
| PT-06 ARM payment stability in fixed window | POINTER -> CRIT-02 (sym #4) |
| PT-07 16-site monthly_payment equivalence | POINTER -> CRIT-02 (sym #2) |
| PT-08 settled transfer decreases principal | POINTER -> CRIT-02 (sym #3) |
| PT-10 escrow not misattributed to principal | POINTER -> HIGH-08 (F-017) |
| PT-11 raw vs A-06-prepared interest series | POINTER -> HIGH-08 (F-018) |
| PT-12 single-debt total_interest == summary | POINTER -> HIGH-08 (F-020) |
| PT-13 loan.py:968 banker's-vs-HALF_UP half-cent | POINTER -> HIGH-08 (F-021) / HIGH-04 |
| PT-14 months_saved vs break_even distinctness | POINTER -> HIGH-08 (F-022) |
| PT-15 payoff_date A == B for an ARM | POINTER -> HIGH-08 (F-023) |
| PT-16 off-engine DTI gross with a raise | POINTER -> MED-06 (F-032) |
| PT-17 calibration-path SS wage-base cap | POINTER -> CRIT-03 (F-037) |
| PT-18 SWR slider == gap math + zero-return weighting | POINTER -> CRIT-04 (F-042 / D6-10) |
| PT-19 employer match card == chart == year-end | POINTER -> HIGH-07 (F-043 / F-055) |
| PT-20 Phase-6 structural/mechanical guards (a..g) | POINTER -> HIGH-05 (PT-20a) / CRIT-01 (PT-20b D6-06) / HIGH-04 (PT-20c D6-07) / CRIT-01 (PT-20d D6-08) / MED-02 (PT-20e D6-09) / MED-01 (PT-20f B6-01, PT-20g B6-02) |

## B.12 Prior-audit backlog (`00_priors.md:804-841` PA-01..PA-30)

| Source ID | Disposition |
| --- | --- |
| PA-01 trend_alert_threshold Range/CHECK | -> HIGH-06 |
| PA-02 rate fields Range/CHECK | -> HIGH-06 |
| PA-03 grid.balance_row scenario.id None-deref 500 | -> CRIT-01 |
| PA-04 compute_slider_defaults float SWR / zero-SWR / zero-return excluded | -> CRIT-04 |
| PA-05 hardcoded 0.04/4.0/7.0 magic fallbacks | -> CRIT-04 (D6-10) |
| PA-06 leap-year 365 vs 366 interest | -> MED-05 |
| PA-07 biweekly paycheck rounding residue ~$2.08/yr | -> MED-05 |
| PA-08 carry_forward missing scenario_id filter | -> LOW-03 |
| PA-09 balance calc done/received post-anchor caveat | -> CRIT-01 |
| PA-10 no penny-level 52+-period balance test | -> HIGH-01 |
| PA-11 no balance-calc idempotency test | -> HIGH-01 |
| PA-12 debt-balance assertion depth | -> MED-07 |
| PA-13 debt-balance sad-path | -> MED-07 |
| PA-14 debt-balance boundary | -> MED-07 |
| PA-15 debt-balance status-machine | -> MED-07 |
| PA-16 debt-balance negative paths | -> MED-07 |
| PA-17 HYSA interest no exact values | -> MED-07 |
| PA-18 HYSA boundary | -> MED-07 |
| PA-19 HYSA full-year compounding (unclear) | -> MED-07 |
| PA-20 paycheck calc no exact / full-year | -> MED-07 |
| PA-21 FICA SS wage cap untested | -> CRIT-03 |
| PA-22 paycheck negative paths | -> MED-07 |
| PA-23 tax calc assertion depth | -> MED-07 |
| PA-24 tax calc annual reconciliation | -> MED-07 |
| PA-25 transfer recurrence boundary | -> MED-07 |
| PA-26 chart data service no value verification | -> MED-07 |
| PA-27 amortization extra-payment directional | -> MED-07 |
| PA-28 calculate_remaining_months zero coverage | -> CRIT-02 |
| PA-29 growth engine directional | -> MED-07 |
| PA-30 pension calc directional | -> MED-07 |

All 30 PA present (line-806 obligation). **P8-d integration proof COMPLETE:**
`00_priors.md:804-841` was read in full this session and each PA-01..PA-30 is
now cited by ID inside its mapped cluster's `Subsumes` block in Part 8.A --
mechanically confirmed this session, the 30 distinct `PA-NN` tokens
(PA-01..PA-30, no gap) appear in Part 8.A: PA-01/PA-02 in HIGH-06; PA-03/PA-09
in CRIT-01; PA-04/PA-05 in CRIT-04; PA-06/PA-07 in MED-05; PA-08 in LOW-03;
PA-10/PA-11 in HIGH-01; PA-12..PA-20/PA-22..PA-27/PA-29/PA-30 in MED-07; PA-21
in CRIT-03; PA-28 in CRIT-02. No PA is `NOT-A-FINDING` and none is silently
dropped (the surjection requires every PA `-> finding`). Three PA prior-status
drifts surfaced by live re-resolution are recorded NOT smoothed: PA-03 (R-8,
P8-b), PA-10/PA-11 (R-9, P8-c), and **PA-08 (R-10, P8-d -- new this session:
`carry_forward_service.py:262` now filters `scenario_id`, the prior `open`
status stale)**; in every case the PA remains mapped to its cluster (the
surjection is unchanged) and only the `00_priors.md` status is the
reconciliation delta, owned by P8-e.

## B.13 Phase 1 standards flags (`01_inventory.md`)

| Source ID | Disposition |
| --- | --- |
| TA-01..TA-11 Jinja arithmetic sites (11) | -> MED-04 |
| JS recompute sites (3 + 3) | -> MED-04 |

## B.14 Resolved intent (`00_priors.md` 0.3 E-01..E-28; `09` Q-01..Q-25 ANSWERED)

| Source ID | Disposition |
| --- | --- |
| E-01..E-28 (28 expectations) | NOT-A-FINDING: resolved-intent-not-a-defect (the governing end state per cluster, recorded as each cluster's `Governing E-NN`; NOT re-litigated) |
| Q-01..Q-07 / A-01..A-07 (plan contradictions) | NOT-A-FINDING: resolved-intent-not-a-defect (A-01 formalized as E-26 -> HIGH-04; A-04/A-05 -> E-18 family) |
| Q-08/A-08 (E-21) | NOT-A-FINDING: resolved-intent-not-a-defect (end state of MED-03) |
| Q-09/A-09, Q-11/A-11, Q-15/A-15, Q-17/A-17, Q-22/A-22, Q-23/A-23 (E-18) | NOT-A-FINDING: resolved-intent-not-a-defect (end state of CRIT-02 / CRIT-01) |
| Q-10/A-10 (E-25) | NOT-A-FINDING: resolved-intent-not-a-defect (end state of CRIT-01) |
| Q-12/A-12 (E-24) | NOT-A-FINDING: resolved-intent-not-a-defect (end state of HIGH-05) |
| Q-13/A-13 (E-20) | NOT-A-FINDING: resolved-intent-not-a-defect (end state of HIGH-03) |
| Q-14/A-14 (E-23) | NOT-A-FINDING: resolved-intent-not-a-defect (settle-orchestrator end state; the dashboard mark-paid surface differences are developer-confirmed non-findings per E-23) |
| Q-16/A-16, Q-20/A-20, Q-21/A-21 (E-19) | NOT-A-FINDING: resolved-intent-not-a-defect (end state of CRIT-01) |
| Q-18/A-18 (E-27) | NOT-A-FINDING: resolved-intent-not-a-defect (end state of HIGH-02) |
| Q-19/A-19 (E-22) | NOT-A-FINDING: resolved-intent-not-a-defect (end state of CRIT-05) |
| Q-24/A-24 (E-28) | NOT-A-FINDING: resolved-intent-not-a-defect (end state of HIGH-06) |
| Q-25/A-25 (E-20/E-28) | NOT-A-FINDING: resolved-intent-not-a-defect (end state of HIGH-03) |

## B.15 Carried tail (`09_open_questions.md` Q-26 sub-2)

| Source ID | Disposition |
| --- | --- |
| Q-26 sub-2 (estimated_retirement_tax_rate NULL-semantics; A-26) | -> LOW-05 (documentation-correction finding recorded; the developer-adjudication contract is carried to Phase 9 unchanged) |

---

# P8-e reconciliation items and cross-phase discrepancies (surfaced, not smoothed)

Contract item 7: cross-phase discrepancies are surfaced in the open; one that
cannot be reconciled becomes a finding against the prior phase with both
citations. P8-e owns the mechanical re-run; P8-a records them here so they are
not lost.

- **R-1 -- the 21-vs-20 DIVERGE count.** Phase 3 C1
  (`03_consistency.md:5986,5991-6016`) classifies **21** findings DIVERGE,
  the set including **F-027** effective_amount ("DIVERGE; SILENT (+E-16);
  Q-08, Q-14"). Phase 7 7.F.3 (`07_test_gaps.md:3869-3875`) builds a
  canonical **20**-DIVERGE set that **omits F-027** and attributes the raw
  `grep -ic DIVERGE` count of 21 to an F-025 substring ("divergence" in an
  AGREE body), not to F-027. The two reconciliations disagree on which ID is
  the 21st. Phase 3's C1 register is the authoritative DIVERGE register; F-027
  is mapped to CRIT-01 regardless (B.1). P8-e reconciles the count and records
  whether Phase 7's 20-set omission of F-027 is a Phase-7 miscount or a
  deliberate scope narrowing.
- **R-2 -- the F-044 miscite.** F-044 carries verdict **AGREE** ("single-path,
  internal verify") in C1, but `07_test_gaps.md:1592-1611` and
  `04_source_of_truth.md:2032-2035` record that the concept
  `contribution_limit_remaining` is **never computed or displayed** (the
  route-resident producer is untested; the pinned growth-engine field is a
  different producer), so F-044's "AGREE" is a miscite localized to
  `02_concepts.md:2169-2200` / `03` F-044; `01_inventory.md` §1.2/§1.5 are
  accurate (Q-24 does not change the §1.5 denominator). F-044 ->
  NOT-A-FINDING: AGREE in B.1 with this flag attached; P8-e records the
  miscite as a documentation-correction against Phase 2/3, not a money
  finding.
- **R-3 -- C3 CRITICAL pre-list vs phase8_plan P8-b CRITICAL scope.** C3
  (`03_consistency.md:6062-6073`) pre-lists the calendar drift (W-065/W-277)
  as a CRITICAL money-impact item ("a different dollar amount than the grid").
  phase8_plan section 4 P8-b scopes exactly five CRITICAL clusters (checking
  #1/#5, loan #2/#3/#4, F-037, F-042, Q-19/W-262) and routes the calendar
  through P8-c via D6-04 (E-27). P8-a provisionally tiers the calendar HIGH-02;
  P8-e re-derives its tier from the severity rubric against the cited
  displayed wrong dollar (contract item 2 -- C3 is an input to re-test, not an
  authority to copy).
- **R-4 -- stale 470-line `savings.py:dashboard` tag.** Phase-6 G4
  (`06_dry_solid.md:2249-2250`) proved the inherited "470-line monolith" tag
  is stale: the route is now 4 lines (extracted to a service). Carried as the
  corrected state (NOT-A-FINDING: superseded, B.8); the un-extracted
  concern-mix that persists in `investment.py` is S6-01 -> MED-01. P8-e
  confirms no later cluster cites the stale 470 figure.
- **R-5 -- Phase-4 denominator + closing-claim correction.** P4-e's §1.5-based
  reconciliation used a 105/106-column denominator
  (`04_source_of_truth.md:2026-2035`); P4-f superseded it with an
  independently model-derived 62-column denominator and CLOSED F-046-SoT
  (`09_open_questions.md:1726-1744`). The Family-D triage closing completeness
  claim (`04:1885-1886`, "No stored-monetary column outside this list") was
  inaccurate (omitted `estimated_retirement_tax_rate`) and is a
  documentation-correction routed under the Q-21 sub-4 / Q-24 protocol; folded
  into LOW-05 / the F-046-SoT documentation correction. P8-e records the
  superseded denominator so no later cluster cites the 105/106 figure.
- **R-6 -- audit-plan output-path typo.** `financial_calculation_audit_plan.md`
  writes `docs/audit/financial_calculations/` (singular) at `:707`, `:1009`,
  `:1025`; the live tree is `docs/audits/` (plural). The wrong directory is
  not created; this file is at the plural path. P8-e records the typo against
  the audit plan; no directory action.
- **R-7 -- audit-plan finding-ID-scheme collision.** Audit-plan `:714`
  ("ID (F-001, F-002, ...)") collides with Phase-3 live `F-001..F-056`. Phase
  8 uses `CRIT/HIGH/MED/LOW-NN`; recorded as an audit-plan-vs-execution
  divergence. P8-e confirms no Part-8.A finding reuses an `F-NNN` ID.
- **R-8 -- PA-03 prior-status drift surfaced by P8-b live re-resolution
  (new this session).** Contract item 1 requires every cited `file:line`
  re-resolved to live source. Re-resolving CRIT-01's PA-03 evidence this
  session: PA-03's specific `grid.balance_row` `scenario.id` None-deref
  500 is **remediated** -- `app/routes/grid.py:404` docstring names "F-099"
  and `:409` `if scenario is None:` / `return "", 204` guards it (the grid
  index path `:177-178` has the same guard). The prior-audit `open` status
  (`00_priors.md:812`) is therefore stale w.r.t. that specific 500. This is
  a prior-phase status discrepancy surfaced in the open, not smoothed:
  PA-03 **remains mapped `-> CRIT-01`** in Part 8.B (the broader
  no-canonical-anchor / anchor-None display family the cluster governs is
  unaffected and persists), so the surjection is unchanged; only PA-03's
  remediation status is the reconciliation delta. P8-e records it as a
  documentation-correction against `00_priors.md`'s PA-03 row (old:
  `open`; current: remediated at `grid.py:404-410`, F-099), not a money
  finding, and confirms no Part-8.A cluster relies on the live presence of
  the PA-03 500.
- **R-9 -- PA-10 / PA-11 prior-status drift surfaced by P8-c live
  re-resolution (new this session).** Contract item 1 requires every cited
  `file:line` re-resolved to live source. Re-resolving HIGH-01's PA-10 /
  PA-11 evidence this session: PA-10 (`00_priors.md:819`, status `open`)
  states "No test verifies penny-level accuracy across 52+ periods", but
  live source has `tests/test_services/test_balance_calculator.py:532`
  `def test_52_period_penny_accuracy(` with `periods = [FakePeriod(i) for
  i in range(52)]` (`:544`); PA-11 (`00_priors.md:820`, status `open`)
  states "No test calls `calculate_balances` twice on identical inputs and
  asserts identical outputs", but live source has
  `test_balance_calculator.py:907`
  `def test_idempotent_same_inputs_same_outputs(`. Both `open`/"no test"
  premises are stale w.r.t. live source. These tests pin the
  **single-producer** `calculate_balances` in isolation (FakePeriod/
  FakeTxn), NOT the cross-page balance-equality invariant HIGH-01 governs
  -- so HIGH-01's substance is unaffected (the three audit-plan cross-page
  greps return 0 matches this session), and **PA-10 / PA-11 remain mapped
  `-> HIGH-01`** in Part 8.B (the surjection is unchanged); only their
  prior `open`/"no test" status is the reconciliation delta. P8-e records
  it as a documentation-correction against `00_priors.md`'s PA-10 / PA-11
  rows (old: `open` / "no test"; current: single-producer penny-accuracy
  and idempotency tests present at `test_balance_calculator.py:532` /
  `:907`, the cross-page lock still absent), not a money finding, and
  confirms no Part-8.A cluster relies on the live absence of those
  single-producer tests (HIGH-01 rests on the cross-page-grep absence,
  independently re-confirmed this session). Surfaced, not smoothed; not
  resolved here (the R-8 PA-03 precedent).
- **R-10 -- PA-08 prior-status drift surfaced by P8-d live re-resolution
  (new this session).** Contract item 1 requires every cited `file:line`
  re-resolved to live source. Re-resolving LOW-03's PA-08 evidence this
  session: PA-08 (`00_priors.md:817`, status `open`) states
  `carry_forward_service` "filters projected transactions by
  `pay_period_id` and `status_id` but not `scenario_id`; if scenarios are
  ever enabled, carry-forward would move transactions across all
  scenarios." Live source: `app/services/carry_forward_service.py:261-263`
  filters `Transaction.pay_period_id == source_period_id,` /
  `Transaction.scenario_id == scenario_id,` / `Transaction.status_id ==
  projected_id,` in `_build_carry_forward_context` (the SELECT that builds
  `ctx.discrete_txns`); the mutating bulk UPDATE (`:405-412`) operates on
  `Transaction.id.in_(template_ids)` drawn from that scenario-filtered set;
  the function signature takes `scenario_id` and its docstring (`:318`)
  states it "Prevents [cross-scenario mixing]". The PA-08 `open`/"missing
  filter" premise is stale w.r.t. live source -- the `scenario_id` filter
  is present at `:262`. LOW-03 also subsumes W-019 (Option-A CD columns
  never added; re-confirmed VIOLATED this session at
  `app/models/interest_params.py`, no `maturity_date`/`term_months`), which
  remains the cluster's still-latent substance, so **PA-08 remains mapped
  `-> LOW-03`** in Part 8.B (the surjection is unchanged); only PA-08's
  remediation status is the reconciliation delta. P8-e records it as a
  documentation-correction against `00_priors.md`'s PA-08 row (old:
  `open`, "missing `scenario_id` filter"; current: filter present at
  `carry_forward_service.py:262`, signature + docstring scenario-scoped),
  not a money finding, and confirms no Part-8.A cluster relies on the live
  absence of that filter. Surfaced, not smoothed; not resolved here (the
  R-8 / R-9 precedent).

---

# Open items deferred to Phase 9 (not Phase 8 findings)

No new finding is added in Phase 8 (contract item 4). The only genuinely-open
item is the **Q-26 sub-2 NULL-semantics developer-adjudication contract**:
A-26 decided the remediation direction (correct the model comment, do not add
a bracket-based fallback), so LOW-05 records it as a documentation-correction
finding; the adjudication contract itself is carried to Phase 9 unchanged
(`09_open_questions.md:1792-1806`). No P8-a ambiguity rose to a new
`09_open_questions.md` entry this session.

## P8-a stop

Part 8.A skeleton and the complete Part 8.B master reverse-index are written;
every section-1 source ID appears exactly once (one cluster, one
`NOT-A-FINDING: <closed-set reason>`, or one section-1-sanctioned `POINTER`
for PT-NN); every Phase-3 DIVERGE (21, incl. F-027) and every Phase-4 UNCLEAR
(7) carries a `-> cluster`; all `PA-01..PA-30` present; the 21-vs-20 DIVERGE
count (R-1) and the F-044 miscite (R-2) are listed as P8-e reconciliation
items. Severity ordering is provisional; P8-b..P8-e add the remaining
schema-3.1 elements and finalize. Session ends.

## P8-d stop

The remaining MEDIUM/LOW clusters now carry every schema-3.1 element, each
with its `Evidence` `file:line` re-resolved to LIVE source this session and
the key line quoted (contract item 1): MED-01 (SOLID structure aggregate;
S6-01/03/04/05/06/07 -- `savings.py`=288 lines, the 4-line delegator
`:110-113`, the relocated monolith `investment.py`=804 lines `:66`/`:366`,
`_DEDUCTION_PATH_TYPES` `investment.py:58`, the 11-key `_load_common_data`
return `year_end_summary_service.py:166-185`), MED-02 (D6-09 inline status
predicate -- `balance_calculator.py:365/411/443`, the twin
`[CREDIT,CANCELLED]` helpers), MED-03 (F-028/F-056 entry-tracked bill row two
bases -- `dashboard_service.py:191` amount vs `:203`/`entry_service.py:405`
remaining; Q-08 carried, NOT resolved), MED-04 (E-16/E-17 standards;
TA-01..TA-11 + 3+3 JS -- `_transaction_cell.html:21`, `_escrow_list.html:37`
== TA-04 == the Phase-6-carried site, `retirement_gap_chart.js:24-25`,
`chart_variance.js:69`), MED-05 (PA-06/PA-07 -- `interest_projection.py:44`
`DAYS_IN_YEAR=Decimal("365")`, `paycheck_calculator.py:133` quantize residue,
both documented trade-offs), MED-06 (F-032 off-engine DTI --
`savings_dashboard_service.py:168-176`), MED-07 (the genuinely-loose PA
test-corpus residue, Phase-7-corrected-state carried), LOW-01 (F-040 dead
code -- zero-consumer grep re-run this session: 0 non-def `app/` consumers;
`tax_calculator.py:233`; `TestLegacyWrapper:510`), LOW-02 (B6-03 --
`transfer_recurrence.py:200-201` vs canonical `transfer_service.py:661/679`),
LOW-03 (W-019 still VIOLATED at `interest_params.py`; PA-08 remediated at
`carry_forward_service.py:262` -- R-10), LOW-04 (the Phase-4 D3-row nit;
`loan_features.py:127` AUTHORITATIVE-implicit), LOW-05 (model comment
`user.py:212-215` vs code `retirement_gap_calculator.py:76/110` -- Q-26 sub-2
carried to Phase 9 unchanged). The line-806 obligation is proven: all
PA-01..PA-30 (30 distinct tokens, no gap) are cited by ID inside their mapped
cluster's `Subsumes` block in Part 8.A; no PA silently dropped; B.12's
closing note records the completed proof. One new cross-phase discrepancy was
surfaced not smoothed (R-10, PA-08 remediated -- the R-8/R-9 precedent; PA-08
stays mapped `-> LOW-03`, surjection unchanged). No new finding added; no
open question resolved (Q-08 in MED-03 and Q-26 sub-2 in LOW-05 carried with
their blocking Q; the Q-26 contract carried to Phase 9 unchanged). One
remediation-direction sentence per cluster, governed by its E-NN. No app run,
no pytest, no code/test/migration/template/JS edit. Severity ordering remains
provisional; P8-e sorts, finalizes IDs, and runs the G1-G9 gate. Session
ends.

---

# Part 8.C -- Verification and consolidation gate (P8-e, trust-but-verify capstone)

Read-only, plan permission mode. No new finding, no new cluster. P8-e verifies,
reconciles, sorts, finalizes, and gates. Every factual claim below was
re-resolved to live source THIS session (not recalled, not trusted from a
prior session's citation). The full `08_findings.md`, `phase8_plan.md`
sections 1-5, the `03_consistency.md` `Verdict:` corpus, `05_symptoms.md` C2 /
roots / handoff, and `04_source_of_truth.md:2129-2146` were re-read this
session per the P8-e prompt.

## 8.C.1 -- Spot-check (>= 15 findings, CRITICAL-weighted, mixed tiers)

17 clusters spot-checked (5 CRIT = 100% of the CRITICAL tier, 5 HIGH, 4 MED,
3 LOW; CRITICAL over-weighted vs the 5/25 population share). For each: the
load-bearing `Evidence` `file:line` was re-resolved to LIVE source this
session (`sed -n`/`grep`/`wc`/`ls`, all read-only) with the key line
verified, AND the severity was re-derived from the rubric (schema 3.3)
against the cited money impact / data-loss path.

| # | Finding | Tier | Load-bearing evidence re-resolved to live source this session | Ev | Severity re-derived from rubric 3.3 | Sev |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | CRIT-01 | CRIT | `grid.py:229` `selectinload(Transaction.entries),` PRESENT; `balance_calculator.py:353-354` `if 'entries' not in txn.__dict__: return txn.effective_amount`; `:383-386` entry-formula `max(...)`; `:72-75` anchor branch; `savings_dashboard_service.py:92-100` query has NO `selectinload(entries)`; `accounts.py:1407-1416` NO `selectinload`; `year_end_summary_service.py:2065-2066` `if ...current_anchor_period_id is None: return None`; `calendar_service.py:471-480` NO `selectinload`; `transaction.py:245` effective_amount return; PA-03 guard live `grid.py:404`(F-099)/`:409-410` | PASS | CRITICAL, Refinement B (sym #1 $160 grid vs $114.29 /savings; sym #5). Cited displayed wrong dollar + page + `file:line` + `05_symptoms.md:195-281` worked example re-read. Rubric: wrong dollar on a budgeting page, divergence not user-visible as error | PASS |
| 2 | CRIT-02 | CRIT | `grep -rEn "\.current_principal\s*=[^=]" app/ scripts/ --include='*.py'` -> exit 1 (ZERO settle-writer); `loan.py:668-674` `_PARAM_FIELDS{"current_principal",...}`+`setattr`; `amortization_engine.py:913` STORED read, `:950-954` ARM scalar, `:977-978` `cur_balance=current_principal`, `:136-142` shrinking `n`; `loan/dashboard.html:104` renders STORED | PASS | CRITICAL, Refinement B (sym #2/#3/#4 ONE family, developer-directed). Cited wrong dollars ($1901.03 vs $1898.50; $300k unchanged; $2,460.45->$2,463.28 vs $2,398.20) + `loan/dashboard.html:104/129` + `05_symptoms.md` re-read | PASS |
| 3 | CRIT-03 | CRIT | `calibration_service.py:106` `def apply_calibration(...)` no cap param; `:129` ss_rate; `:139-141` flat `(gross*ss_rate).quantize(...)`; `grep ss_wage_base\|cumulative_wages calibration_service.py` -> exit 1; `tax_calculator.py:294,300-306` cap ENFORCED `if cumulative >= ss_wage_base: ss_tax = ZERO`; `paycheck_calculator.py:160-173` calibration gate | PASS | CRITICAL, C3 candidate re-derived (not copied). +$7,905/yr FICA overstatement, net -$744/over-cap period; `03_consistency.md:3072-3091` worked example re-read; wrong dollar on the paycheck projection, reachable for the documented use case, no error | PASS |
| 4 | CRIT-04 | CRIT | `retirement_dashboard_service.py:217-221` `or "0.04"` truthiness at `:220`; `:240` chart income uses that swr; `:304-309` slider uses `is None`; `:321` `if params and params.assumed_annual_return:` truthiness; `:323-328` weighted-return | PASS | CRITICAL, C3 candidate re-derived. $4,000/mo phantom income; 7.00% vs true 3.50% blended return; `03_consistency.md:3551-3573` re-read; wrong dollars + wrong rate on the retirement page, no error | PASS |
| 5 | CRIT-05 | CRIT | `transactions.py:534-535` income->RECEIVED, `:610` assign; `ref_seeds.py:81` RECEIVED `is_settled:True,is_immutable:True`; `archive_helpers.py:29-38` predicate `in_([paid_id,settled_id])` (RECEIVED absent); `:53-60` twin enumeration; `templates.py:581` sole guard, `:612-618` unconditional bulk delete, `:636` "permanently deleted" | PASS | CRITICAL, Refinement A (data-loss): irreversible silent destruction of settled (`is_settled=True`) RECEIVED income on a normal user delete; the harm is the loss, not a wrong number; irreversible-destruction path cited end-to-end | PASS |
| 6 | HIGH-01 | HIGH | 3 audit-plan cross-page-equality greps re-run over live `tests/`: `auth_client.get...grid...accounts` -> exit 1, `...grid...savings` -> exit 1, `grid.*==.*savings|checking.*==.*accounts` -> exit 1 (all 0 matches); near-miss `test_accounts.py:2211 def test_checking_detail_matches_grid_balance(` live; R-9: `test_balance_calculator.py:532 def test_52_period_penny_accuracy(`+`:544`, `:907 def test_idempotent_...` EXIST (PA-10/PA-11 `open` stale) | PASS | HIGH, rubric "absence of any regression lock for a proven CRITICAL". No displayed wrong dollar of its own (test-gap, not a producer) -> HIGH not CRITICAL | PASS |
| 7 | HIGH-02 | HIGH | `calendar_service.py:435 def _compute_month_end_balance(`; `:449-450` anchor-None `return Decimal("0")`; `:461-465` last-period-ending-on-or-before-month-end loop (days-stale); `:471-480` query NO `selectinload(entries)`; `:482-489` `calculate_balances(...)` then `balances.get(target_period.id,...)` | PASS | HIGH, rubric "computed drift sufficient to produce a wrong dollar under realistic inputs". No calendar-specific Phase-3/5 hand-derived wrong dollar (W-277 axis UNKNOWN-Q-18); contract item 2 downgrades absent a cited figure -> HIGH (R-3 surfaced, see 8.C.3) | PASS |
| 8 | HIGH-04 | HIGH | `ls app/utils/ \| grep -i money\|round` -> exit 1; `grep -rn "def round_money" app/` -> exit 1 (E-26 helper absent); `loan.py:966-968` `(...).quantize(Decimal("0.01"))` NO `rounding=` -> ROUND_HALF_EVEN (D6-07 register-(b) row 24) | PASS | HIGH, rubric "computed drift sufficient to produce a wrong dollar". A cent (not dollars-wide) at a half-cent boundary; no single developer-reported symptom -> HIGH | PASS |
| 9 | HIGH-06 | HIGH | `validation.py:1787-1788` `trend_alert_threshold` `Range(min=1,max=100)` vs `user.py:198` CHECK `>= 0 AND <= 1`; `interest_params.py:60` `apy ... server_default="0.04500"`; `investment_params.py:80-84` float `default=0.07000` + `annual_contribution_limit` nullable; tri-consumer `investment.py:231/305/667` truthiness vs `growth_engine.py:206` `is not None` | PASS | HIGH, rubric "computed drift ... under realistic inputs" (E-28). Reachable wrong number on a first save omitting `apy` (silent 4.5%); no single developer-reported symptom -> HIGH | PASS |
| 10 | HIGH-08 | HIGH | `balance_calculator.py:253` anchor base, `:274` interest_portion, `:277` principal_portion; `amortization_engine.py:740/:749` HALF_UP vs `loan.py:966-968` banker's (F-021 half-cent); `loan.py:957-959` vs `amortization_engine.py:739` distinct months pair; `:642` total_interest, `:645` payoff_date, `:753 def calculate_payoff_by_date(` no payments/anchor param | PASS | HIGH, rubric "computed drift ... under realistic inputs"; E-18 family downstream of CRIT-02's root; latent figures phase8_plan deliberately split from CRIT-02's symptom triple; no developer-reported symptom -> HIGH | PASS |
| 11 | MED-01 | MED | `wc -l app/routes/savings.py` = 288; `savings.py:110-113` 4-line delegator; `savings_dashboard_service.py:61 def compute_dashboard_data(`; `wc -l app/routes/investment.py` = 804; `:58 _DEDUCTION_PATH_TYPES`, `:66 def dashboard(`, `:366 def growth_chart(`; `year_end_summary_service.py:90 def _load_common_data(` | PASS | MEDIUM, rubric "DRY/SOLID ... no current wrong number". Every member Phase-6 NONE->structural-only; latent S6-01 path unexercised -> MEDIUM not HIGH (also confirms R-4: 470 tag stale, savings.py=288) | PASS |
| 12 | MED-04 | MED | `grid/_transaction_cell.html:21` `{% set remaining = t.estimated_amount - es.total %}`; `loan/_escrow_list.html:37` `{{ ...format(comp.annual_amount\|float / 12) }}` (TA-04 == Phase-6 handoff site); `retirement_gap_chart.js:24-25` `pension+investment`/`Math.max(0,preRetirement-covered)`; `chart_variance.js:69` `var diff = act - est;` | PASS | MEDIUM, rubric "standards violations (Jinja/JS arithmetic) numerically consistent today". Phase 3 AGREE-numerically, no cited wrong dollar; F-027 +E-16 wrong-dollar home is CRIT-01 cross-ref -> MEDIUM | PASS |
| 13 | MED-05 | MED | `interest_projection.py:44 DAYS_IN_YEAR = Decimal("365")`; `paycheck_calculator.py:132 pay_periods_per_year = ... or 26`, `:133 gross_biweekly = (annual_salary / pay_periods_per_year).quantize(` | PASS | MEDIUM, rubric: real systematic error under realistic inputs but sub-dollar/low-single-dollar per year and documented in-source as accepted trade-offs -> not CRIT/HIGH, not LOW (real systematic) -> MEDIUM | PASS |
| 14 | MED-07 | MED | `00_priors.md:821` PA-12 row / `:839` PA-30 row / `:841` per-doc counts (`test_audit`=21); `test_pension_calculator.py:63 ==Decimal("38387.50")`, `:64 ==Decimal("3198.96")`, `:146 ==Decimal("606.80")` (exact pins -> PA-30 "directional" stale); `test_savings_dashboard_service.py:1297 is not None`/`:1298 isinstance`/`:1338 ==Decimal("0.0")` | PASS | MEDIUM, rubric "missing tests for important invariants ... no current wrong number attributable here". Genuinely-loose residue carried as Phase-7 corrected state -> MEDIUM | PASS |
| 15 | LOW-01 | LOW | `grep -rn calculate_federal_tax app/ \| grep -v "def ..." \| wc -l` -> 0 (zero `app/` consumers); `tax_calculator.py:215 def calculate_federal_tax(`, `:233` standard-deduction-only, `:234` returns annual; `test_tax_calculator.py:510 class TestLegacyWrapper:`, `:518 ==Decimal("5700.00")` | PASS | LOW, rubric "dead code carrying an inert divergence (F-040)" (explicit). Zero `app/` consumers -> unreachable -> no displayed wrong dollar -> LOW | PASS |
| 16 | LOW-03 | LOW | `interest_params.py` cols = `:40 id/:41 account_id/:60 apy/:61 compounding_frequency`; `grep maturity_date\|term_months interest_params.py` -> exit 1 (W-019 still VIOLATED); PA-08 `carry_forward_service.py:261-263` `scenario_id == scenario_id` PRESENT at `:262`, `:318` docstring scenario-scoped, `:405-412` bulk UPDATE scoped (R-10: PA-08 `open` stale) | PASS | LOW, rubric "minor ... low blast radius" / latent missing feature no wrong number. W-019 absent feature (no number wrong); PA-08 remediated -> LOW | PASS |
| 17 | LOW-05 | LOW | `user.py:212-215` comment "NULL = unset, fall back to current bracket-based estimate" (live `:212-215`, was cited `:215-216` -- model shifted, behaviour as described); `:242` column nullable; `retirement_gap_calculator.py:37 def calculate_gap(`, `:76/:110 if estimated_tax_rate is not None:`, `:79 *(1-estimated_tax_rate)` (None -> skipped, no fallback); `retirement_dashboard_service.py:223-224/:234` | PASS | LOW, rubric "formatting, naming ... low blast radius" -- a model-comment-vs-code divergence; A-26 decided the doc-correction direction; no money impact -> LOW (the contract itself is the Phase-9 carried tail, NOT decided here) | PASS |

**Spot-check result: 17/17 evidence re-resolved to live source (100%); 17/17
severity re-derived to the recorded tier from the rubric (100%).** Threshold
is 100% on both axes; both met. Zero stale-citation misses, zero
severity-not-rubric-supported misses. No tier session is reopened. Minor
in-doc citation notes (CRIT-01 calendar `:461-466`->`:463-466`; HIGH-07
`:188`->`:187-189`; LOW-05 `:215-216`->`:212-215`) were already recorded
inline by P8-b..P8-d with the old and current location and "behaviour
exactly as described"; each re-confirmed this session, none is a verdict or
severity change.

## 8.C.2 -- Surjection reconciliation (G4), re-run mechanically this session

- **Phase-3 F-001..F-056:** B.1 carries 56 distinct F-IDs (`F-001`..`F-056`,
  no gap; mechanically counted this session). 58 table rows because F-054
  (YG1/YG2) and F-056 (`entry_sum_total`/`entry_remaining`) are
  section-1-sanctioned split rows -- each sub-concept has exactly one
  disposition, which is a single disposition per source ID, not a double-map.
- **21 Phase-3 DIVERGE -> cluster, zero `NOT-A-FINDING`:** re-derived from the
  authoritative C1 register (`03_consistency.md:5991-6016`, re-read this
  session): F-001/002/003/005/009/013/014/015/017/018/020/021/022/023/026/
  **027**/032/037/042/043/055 = 21. Each carries a `-> CRIT/HIGH/MED/LOW` in
  B.1 (F-027 -> CRIT-01, the E-16 facet cross-ref MED-04). Confirmed.
- **9 UNKNOWN + F-056 sub -> cluster:** F-004/006/007/008/016/028/051/052/
  054-YG1/056-`entry_remaining` each `-> cluster` (B.1). Confirmed.
- **7 Phase-4 UNCLEAR + the Q-26 GAP -> cluster, zero `NOT-A-FINDING`:**
  `current_anchor_period_id`(Q-20)->CRIT-01, `current_principal`(Q-22)/
  `interest_rate`(Q-23)->CRIT-02, the four `effective_*_rate`(Q-25)->HIGH-03,
  `estimated_retirement_tax_rate` GAP(Q-26)->LOW-05. Matches
  `04_source_of_truth.md:2126-2127` ("7 columns ... Q-20, Q-22, Q-23, Q-25
  ... plus Q-26 for the GAP"), re-read this session. Confirmed.
- **All 30 PA-01..PA-30 present:** 30 distinct `PA-NN` tokens in B.12
  (PA-01..PA-30, no gap, mechanically counted); B.12's closing note proves
  each is cited by ID inside its mapped cluster's `Subsumes`. Confirmed.
- **20 Phase-6 D6/S6/B6:** D6-01..D6-10, S6-01..S6-07, B6-01..B6-03 = 20
  distinct tokens in B.7. Confirmed.
- **Phase-7 PT POINTER:** 19 `POINTER ->` disposition rows in B.11
  (PT-01..PT-08, PT-10..PT-20; PT-20 folds a..g); PT-09 is
  section-1-sanctioned omitted (Phase 7 never authored it; the only PT-09
  occurrence in the doc is the B.11 header "PT-09 omitted"). Not an orphan.
  Confirmed.
- **NOT-A-FINDING reasons are the closed set:** the only reason tokens used
  in disposition rows are `AGREE`/`AUTHORITATIVE`/`COVERED`/`HOLDS`/
  `superseded-by-A-NN`/`resolved-intent-not-a-defect`. The lone bare
  "superseded" token (line 2643) is R-4 prose shorthand referencing the B.8
  `superseded-by-A-NN` disposition, not a disposition row -- a cosmetic
  prose note, not a closed-set violation.
- **Zero double-maps:** the double-cluster-arrow scan flags exactly one row
  (`budget.transactions.actual_amount`, B.5): its disposition is the single
  `NOT-A-FINDING: AUTHORITATIVE`; the parenthetical `F-027 -> CRIT-01 /
  F-028 -> MED-03` is a cross-reference to where the *bypass surface*
  (separate source IDs, each singly mapped in B.1) is handled, not a second
  mapping of `actual_amount`. No genuine double-map.
- **Phase-7 non-COVERED accounted:** every non-COVERED Part-7.A concept in
  B.9 carries a `-> cluster`; the 28 COVERED concepts -> `NOT-A-FINDING:
  COVERED`; the cross-page meta-gap -> HIGH-01.

**G4 verdict: PASS.** Every section-1 source ID maps to exactly one cluster,
one closed-set `NOT-A-FINDING`, or one section-1-sanctioned `POINTER`; zero
orphans; zero double-maps; all PA-01..PA-30 integrated; every Phase-3 DIVERGE
and Phase-4 UNCLEAR `-> finding`.

## 8.C.3 -- Cross-phase reconciliation (G6)

### R-1 -- the 21-vs-20 DIVERGE count: RESOLVED, delta = F-027

Both sets enumerated this session:

- **Phase-3 C1 (authoritative DIVERGE register, `03_consistency.md:5991-6016`,
  re-read):** 21 -- F-001, F-002, F-003, F-005, F-009, F-013, F-014, F-015,
  F-017, F-018, F-020, F-021, F-022, F-023, F-026, **F-027**, F-032, F-037,
  F-042, F-043, F-055.
- **Phase-7 7.F.3 / Part-7.B canonical set (`07_test_gaps.md:3797-3800`,
  `:3869-3875`, re-read):** 20 -- the same set MINUS **F-027**.
- **Delta = F-027** (`effective_amount`, "~43-site bypass sweep").

Cause, re-resolved this session: F-027's verdict is NOT phrased as a standard
`- Verdict: **DIVERGE**` line; it is the consolidated block
`03_consistency.md:2240-2269` -- `:2260` "Overall: **DIVERGE** label for the
concept", `:2266` "If DIVERGE: classification: SILENT_DRIFT". An independent
re-grep this session of `Verdict:` lines containing `DIVERGE` returns 20
standard lines PLUS the F-025 `:1923` false positive ("AGREE ... no
divergence" -- the substring, not a verdict); it does NOT catch F-027.
Phase 7 concluded its canonical set is 20 by assuming the sole over-count of
the raw `grep -ic DIVERGE`=21 was the F-025 `:1923` substring, and never
acknowledged F-027. Two errors coincidentally each equal 1 and masked the
omission: the Verdict-word grep under-counts by 1 (misses F-027's non-standard
verdict phrasing) while the raw `grep -ic` over-counts by 1 (the F-025
substring).

**Resolution:** the authoritative count is **21**. Phase 3's C1 register is
the authoritative DIVERGE register (audit-plan: Phase 4 / Phase 5 load C1,
`03_consistency.md:5984`) and it classes F-027 DIVERGE explicitly at
`:6010`. Phase 7's 20-set is a **miscount of the canonical DIVERGE set**, not
a deliberate scope narrowing -- its own 7.F.3 text claims the 20-set "exactly
matches the Part 7.B canonical 20-set" and attributes the lone over-count
solely to the F-025 substring, with no F-027 exclusion rationale; a
deliberate narrowing would have stated one. **Recorded as a
documentation-level finding against Phase 7's 7.F.3 reconciliation, with both
citations (`03_consistency.md:6010` / `:2260` DIVERGE vs
`07_test_gaps.md:3869-3875` 20-set).** No money impact; no disposition
change: F-027 -> CRIT-01 in B.1 regardless, and B.1's DIVERGE coverage check
already enumerates all 21 incl. F-027 explicitly. Surfaced in the open, not
smoothed; not averaged into a single number.

### R-2 -- the F-044 miscite: CARRIED CORRECTED (+ R-2a citation drift)

F-044's verdict is **AGREE** ("single-path, route-resident `limit - ytd` at
`investment.py:173-181`", re-read this session at `03_consistency.md:3695`).
The miscite: F-044's "AGREE" implies the concept is computed/displayed, but
`04_source_of_truth.md:2032-2035` (re-read) records the
`contribution_limit_remaining` miscite is localized to
`02_concepts.md:2169-2200` / `03` F-044; `01_inventory.md` §1.2/§1.5 are
accurate (Q-24 does not change the §1.5 denominator). B.1 maps F-044 ->
`NOT-A-FINDING: AGREE` with the inline `**F-044 miscite, reconciliation item
R-2**` flag. The prior-phase AGREE verdict is preserved; the miscite is
recorded as a documentation-correction against Phase 2/3, not propagated as a
money finding; the surjection is unchanged. **Carried corrected.**

- **R-2a (new this session, surfaced not smoothed -- contract item 1/7):**
  the R-2 note's pointer `07_test_gaps.md:1592-1611` is itself a stale
  citation. That live range is FICA SS-cap text (CRIT-03 territory), re-read
  this session. The actual Phase-7 record that
  `contribution_limit_remaining`'s route-resident producer is untested (the
  pinned growth-engine field being a different producer) is at
  `07_test_gaps.md:2649` (verdict roll-up row) and `:2018-2072` (Concept 4
  body). Recorded with both the stale (`:1592-1611`) and current
  (`:2649`/`:2018-2072`) location; the R-2 substance is independently
  confirmed by the current location plus `04_source_of_truth.md:2032-2035`
  and the F-044 verdict at `03_consistency.md:3695`. No disposition change
  (F-044 -> `NOT-A-FINDING: AGREE` unchanged); no money impact; a
  P8-internal citation-correction, not a new application finding.

### R-3 -- C3 calendar CRITICAL vs HIGH-02: RESOLVED at the rubric

C3 (`03_consistency.md:6072`) pre-listed the W-065/W-277 calendar drift as a
CRITICAL money item. Per contract item 2 / G3, C3 is an input to re-test, not
an authority to copy. P8-e re-derives the calendar tier from the rubric: the
calendar entries-load axis inherits CRIT-01's $45.71-class gap (no
calendar-specific Phase-3/5 hand-derived displayed wrong dollar), and the
W-277 period-selection axis is UNKNOWN-Q-18; absent a calendar-specific cited
displayed wrong dollar the rubric (contract item 2) downgrades it to **HIGH**.
HIGH-02 is the correct tier; the C3 "CRITICAL" pre-flag is the candidate
input the rubric re-tests, exactly the load-bearing Phase-8 rule. Surfaced,
resolved at the rubric.

### R-4 -- stale 470-line `savings.py:dashboard` tag: CARRIED as Phase 6's corrected state

All five "470" occurrences in `08_findings.md` (lines 1387, 1411, 2450, 2640,
2645) are the carried-corrected references: MED-01's `Subsumes`/`Evidence`
explicitly label it "proven stale", B.8 disposition `NOT-A-FINDING:
superseded-by-A-NN`, and R-4 itself. No CRIT/HIGH/MED/LOW cluster cites 470
as a live SRP violation. Re-resolved this session: `wc -l
app/routes/savings.py` = **288**; `savings.py:110-113` is a 4-line delegator
into `savings_dashboard_service.compute_dashboard_data`; the live relocated
root is `investment.py` = **804** lines (S6-01 -> MED-01). Confirmed: no
later cluster cites the stale 470 figure.

### R-5 -- Phase-4 denominator + closing-claim: carried corrected

No CRIT/HIGH/MED/LOW cluster cites the superseded 105/106-column denominator;
the Family-D triage closing-completeness correction (omitted
`estimated_retirement_tax_rate`) is folded into LOW-05 / the F-046-SoT
documentation correction. Confirmed against B.5 (one `Family D triage block
... NOT-A-FINDING: AUTHORITATIVE` row, R-5 cross-ref) and LOW-05's `Subsumes`.

### R-6 / R-7 -- audit-plan typo + ID-scheme collision: confirmed

R-6: this file is at the plural `docs/audits/...` path; the singular
`docs/audit/...` directory was not created (re-confirmed: no such directory).
R-7: no Part-8.A finding reuses an `F-NNN` ID -- Part 8.A uses
`CRIT/HIGH/MED/LOW-NN` exclusively (mechanically verified: the 25 Part-8.A
headers are all tiered IDs); `F-NN` appears only as inherited source IDs in
`Subsumes`/Part 8.B. Both confirmed.

### R-8 / R-9 / R-10 -- PA prior-status drifts: surfaced, not smoothed; surjection unchanged

Each re-confirmed this session at live source: **R-8** PA-03 grid
`scenario.id` None-deref remediated (`grid.py:404` F-099 docstring, `:409-410`
guard); **R-9** PA-10/PA-11 single-producer tests EXIST
(`test_balance_calculator.py:532` `test_52_period_penny_accuracy`, `:907`
`test_idempotent_same_inputs_same_outputs`) while the cross-page lock is
still absent (3 greps 0 matches, spot-check #6); **R-10** PA-08
`scenario_id` filter present (`carry_forward_service.py:262`). In every case
the PA remains mapped to its cluster (PA-03->CRIT-01, PA-10/PA-11->HIGH-01,
PA-08->LOW-03); only the `00_priors.md` status line is the reconciliation
delta; recorded as documentation-corrections against `00_priors.md`, not
money findings, surjection unchanged.

### Symptom -> finding map vs Phase 3 C2 / Phase 4 drift register / Phase 5

Re-read this session and cross-checked:

| Symptom | Phase 3 C2 (`03:6049-6060`) | Phase 4 drift reg (`04:2138-2142`) | Phase 5 root (`05` best-evidence) | Phase 8 (B.2) |
| --- | --- | --- | --- | --- |
| #1 | F-009 + W-277 | F-002/F-003/F-001 (Q-16/Q-20) | F-009 + W-277 | -> CRIT-01 |
| #2 | F-013 | F-013/F-026 (Q-22/Q-23/Q-17) | F-013 on F-026 | -> CRIT-02 |
| #3 | F-014 | F-014/F-015/F-016 (Q-22) | F-014 | -> CRIT-02 |
| #4 | F-026 | F-026 (Q-17/Q-22/Q-23) | F-026 | -> CRIT-02 |
| #5 | F-001 + F-008 | F-001/F-003/F-008/F-015/F-016 | union of three (F-001+F-008) | -> CRIT-01 (loan-base facet cross-ref CRIT-02) |

All constituent F-IDs route consistently: F-009/F-001/F-002/F-003 -> CRIT-01;
F-013/F-014/F-015/F-016/F-026/F-008 -> CRIT-02 (B.1). #5's F-008 loan-base
facet cross-ref to CRIT-02 is correct (F-008 -> CRIT-02 in B.1). Phase 5's
#2/#3/#4 collapse-onto-one-column (E-18) -> the single CRIT-02 family,
developer-directed, confirmed. **No contradiction with any prior phase.**

**G6 verdict: PASS.** The DIVERGE count discrepancy is resolved (delta =
F-027; authoritative = 21; Phase-7 7.F.3 miscount recorded as a finding
against Phase 7 with both citations), the F-044 miscite is carried corrected
(plus the R-2a in-doc citation drift surfaced with both locations), the stale
470 tag is carried as Phase 6's corrected state with no later cluster citing
it, and the symptom->finding map matches Phase 3 C2 / Phase 4 drift register
/ Phase 5 with zero contradiction. Every discrepancy surfaced in the open;
none averaged away.

## 8.C.4 -- Severity sort + ID finalization

Part 8.A is in strict severity-descending order, mechanically verified this
session (the 25 `### ` headers in file order): **CRITICAL** CRIT-01..CRIT-05
(5) -> **HIGH** HIGH-01..HIGH-08 (8) -> **MEDIUM** MED-01..MED-07 (7) ->
**LOW** LOW-01..LOW-05 (5) = 25 clusters.

Within CRITICAL, the audit-plan section-8 rule ("developer's reported
symptoms first, then the other CRITICALs, encoded in the ID") is satisfied
by the existing numbering: CRIT-01 carries reported symptoms #1/#5 (lowest
symptom number #1), CRIT-02 carries reported symptoms #2/#3/#4 (lowest #2),
then the latent CRITICALs CRIT-03 (F-037), CRIT-04 (F-042), CRIT-05
(Q-19/W-262 data-loss) in C3-pre-list order. The provisional IDs already
encode the mandated sort; **no renumbering is required**. The CRITICAL
membership is exactly {CRIT-01..CRIT-05}: the spot-check re-derived 5/5
CRITICAL severities from the rubric (all PASS), every C3 candidate was
re-tested (F-037/F-042 -> CRIT-03/04; W-065/W-277 -> HIGH-02 per R-3), and
every sampled non-candidate confirmed its lower tier is rubric-correct (no
missed CRITICAL). **Severity ordering is finalized; the "provisional"
qualifier is lifted.** Final ID list: CRIT-01, CRIT-02, CRIT-03, CRIT-04,
CRIT-05, HIGH-01..HIGH-08, MED-01..MED-07, LOW-01..LOW-05.

## 8.C.5 -- Acceptance gate G1-G9

| Gate | Evidence | Verdict |
| --- | --- | --- |
| **G1** | `08_findings.md` exists, 25-cluster Part 8.A + Part 8.B (B.1-B.15) + Part 8.C (this section). Mechanically verified: 25 `### CRIT/HIGH/MED/LOW-NN` headers, each carrying all 11 schema-3.1 elements (ID header + the 10 bolded blocks: Severity+rubric, Category, Plain-language, Subsumes, Governing E-NN, Evidence, Phase-doc pointers, Open questions, Remediation direction, Blast radius); Part 8.A sorted severity-desc then symptom-first within CRITICAL (8.C.4) | **PASS** |
| **G2** | Every Part-8.A `Evidence` block is tagged "(re-resolved to live source this session, key line quoted)" by P8-b..P8-d; P8-e spot-checked 17/25 (CRITICAL-weighted) at 100% live re-resolution (8.C.1); the three minor citation notes carry old+current location with "behaviour exactly as described"; no citation carried from a phase doc without re-resolution | **PASS** |
| **G3** | Every `CRIT-NN` cites the displayed wrong-dollar (CRIT-01 $160/$114.29; CRIT-02 $1901.03/$1898.50, $300k, $2,460.45->$2,463.28 vs $2,398.20; CRIT-03 +$7,905/yr; CRIT-04 $4,000/mo, 7.00%/3.50%) or the data-loss path (CRIT-05 Refinement A), the page + `file:line`, and the Phase-3/5 worked example re-read this session; severity re-derived from the rubric not copied from C3 (each Severity block states this); every C3 candidate re-tested (incl. R-3 calendar downgrade) and sampled non-candidates checked for a missed CRITICAL (none) | **PASS** |
| **G4** | 8.C.2: every section-1 ID -> exactly one cluster / closed-set `NOT-A-FINDING` / sanctioned `POINTER`; zero orphans; zero double-maps; all PA-01..PA-30 integrated; 21 Phase-3 DIVERGE and 7 Phase-4 UNCLEAR+GAP all `-> finding` | **PASS** |
| **G5** | 8.C.1: 17 findings spot-checked (5 CRIT/5 HIGH/4 MED/3 LOW, CRITICAL-weighted), 17/17 re-resolved to live source AND 17/17 re-derived to the recorded severity tier; table + count shown; 100% both axes | **PASS** |
| **G6** | 8.C.3: 21-vs-20 resolved (delta F-027; authoritative 21; Phase-7 7.F.3 miscount recorded as a finding against Phase 7 with both citations); F-044 miscite carried corrected (+ R-2a citation drift surfaced with both locations); stale 470 tag carried as Phase-6 corrected state, no later cluster cites it; symptom->finding map matches Phase 3 C2 / Phase 4 drift register / Phase 5, zero contradiction; each surfaced in the open, none smoothed | **PASS** |
| **G7** | Every Part-8.A cluster carries exactly one `Remediation direction:` sentence consistent with its `Governing E-NN` (verified across all 25 while reading); P8-e is read-only -- no fix diff produced this session | **PASS** |
| **G8** | No new auditor-invented finding added in P8-e (verify/reconcile/sort/finalize only); the R-1..R-10 + R-2a items are documentation-corrections against prior phases (the contract-item-7 mechanism), change no disposition, add no cluster; UNKNOWN/UNCLEAR/BLOCKED preserved with blocking Q and NOT resolved (HIGH-03 Q-25, MED-03 Q-08, LOW-05 Q-26 sub-2); Q-26 NULL-semantics tail carried to Phase 9 unchanged; no new `09_open_questions.md` entry required (no new ambiguity arose in P8-e) | **PASS** |
| **G9** | 8.C.7 `git status`: only `docs/audits/financial_calculations/08_findings.md`; no source/test/migration/template/JS file touched (all P8-e source access was read-only `sed`/`grep`/`wc`/`ls`) | **PASS** |

**G1-G9: 9/9 PASS.**

## 8.C.6 -- Handoff to Phase 9

- **Q-26 sub-2 carried tail (the only Phase-8 carried-open item):** the
  `auth.user_settings.estimated_retirement_tax_rate` NULL-semantics contract
  -- the `user.py:212-215` model comment promises a bracket-based fallback
  when NULL; `retirement_gap_calculator.py:76/:110` apply NO tax adjustment
  when None. A-26 decided the documentation-correction direction (fix the
  comment to "NULL = no retirement-tax adjustment applied"; do NOT build the
  fallback), recorded as finding LOW-05. **The product-contract question --
  whether a bracket-based fallback SHOULD exist -- is carried to Phase 9
  UNCHANGED, exactly as Phases 5/6/7 carried it (G8). Phase 8 does not decide
  it.**
- **UNKNOWN/UNCLEAR/BLOCKED findings whose blocking Q the developer must
  answer before remediation can be planned:**
  - **HIGH-03** -- the four `salary.calibration_overrides.effective_*_rate`
    columns: **Q-25** (frozen pay-stub snapshot vs live-derived rate). The
    finding is recorded WITH Q-25; the auditor does not pick a side. The
    developer must answer Q-25 before HIGH-03's remediation shape is fixed.
  - **MED-03** -- the entry-tracked bill row two-base inconsistency: **Q-08**
    (entry "remaining/over-budget" anchored on plan `estimated_amount` vs
    actual spend). The cross-anchor row inconsistency holds regardless of
    Q-08, but Q-08 governs the remediation base; carried, not resolved.
  - **LOW-05** -- **Q-26 sub-2** (above).
  - The CRITICAL/HIGH balance and loan clusters (CRIT-01, CRIT-02, HIGH-02,
    HIGH-08) subsume Phase-3 UNKNOWN / Phase-4 UNCLEAR members
    (Q-10/Q-11/Q-15/Q-16/Q-17/Q-20/Q-22/Q-23), but all are ANSWERED
    (A-10..A-23) and locked by E-18/E-19/E-25/E-27, consumed as the governing
    end state and NOT re-litigated; no developer decision blocks their
    remediation direction (the E-NN already states the end state).
- **Remediation planning is a separate post-audit exercise.** Phase 8 is the
  prioritized, root-cause-clustered, severity-sorted findings report. Each
  cluster carries exactly one remediation-DIRECTION sentence governed by its
  E-NN -- it is a direction, not a plan. No diff, no step list, no migration
  sketch, no sequencing, no effort estimate is produced or implied here.
  Converting these 25 clusters into an ordered remediation plan (and
  answering Q-08 / Q-25 / the Q-26 sub-2 contract first) is explicitly
  out of audit scope and is a distinct exercise the developer undertakes
  after the audit (audit-plan section 8 final paragraph, section 11).

## 8.C.7 -- git status (only audit docs changed)

```
$ git status --short
?? docs/audits/financial_calculations/08_findings.md
```

Sole entry. `08_findings.md` is the only path in the working tree (untracked,
created by P8-a..P8-d and appended by P8-e). No tracked-file diff. No source,
test, migration, template, or JS file was touched -- all P8-e source access
was read-only (`sed -n` / `grep` / `wc` / `ls`); pytest was never invoked;
the app was never run. G9 PASS.

## Phase 8 completion

**Phase 8 complete.** Gate roll-up: **G1 PASS, G2 PASS, G3 PASS, G4 PASS, G5
PASS (17/17 evidence + 17/17 severity, 100% both axes), G6 PASS, G7 PASS, G8
PASS, G9 PASS -- 9/9.**

Spot-check 17/17 at live source on both axes (CRITICAL-weighted, all 5
CRITICAL clusters re-resolved and re-derived); the surjection re-run
mechanically (every section-1 source ID exactly once, zero orphans, zero
double-maps, all PA-01..PA-30 integrated, every Phase-3 DIVERGE and Phase-4
UNCLEAR `-> finding`); the 21-vs-20 DIVERGE discrepancy resolved (delta =
F-027; authoritative count 21; Phase-7 7.F.3 miscount recorded as a
documentation-level finding against Phase 7 with both citations, no
disposition change); the F-044 miscite carried corrected with the new R-2a
in-doc citation drift surfaced (both locations recorded); the stale 470-line
tag carried as Phase 6's corrected state with no later cluster citing it;
the symptom->finding map confirmed against Phase 3 C2 / Phase 4 drift
register / Phase 5 with zero contradiction; Part 8.A severity-sorted and the
provisional ID qualifier lifted; the Q-26 sub-2 NULL-semantics tail carried
to Phase 9 unchanged; remediation planning explicitly deferred as a separate
post-audit exercise. No new finding added; no open question resolved; no
gate failed; no tier session reopened. Source, tests, migrations, templates,
and static files untouched; `git status` shows only
`docs/audits/financial_calculations/08_findings.md`.

## P8-e stop

Part 8.C appended; "Phase 8 complete" recorded with the G1-G9 roll-up (9/9
PASS). The financial-calculation audit's Phase 8 deliverable -- the
severity-sorted, root-cause-clustered, surjection-proven findings report --
is complete. Session ends.

---

# Remediation addendum -- R-9 / R-10 reconciliation closure (Commit 35, 2026-05-21)

This addendum is appended by the remediation work (`remediation_plan.md`
Section 9, Commit 35); it does not retroactively edit any Phase-8 finding or
reconciliation item. Each Phase-8 R-item above is a documentation-correction
against an earlier phase that surfaced a `00_priors.md` PA-status drift in
the open and explicitly did NOT resolve it (the "surfaced not smoothed"
contract, P8-e item 7). The remediation work has now consumed two of those
items end-to-end; this section records that consumption so a future reader
of `08_findings.md` can trace each surfaced drift to its closing commit
without re-deriving the live-source state.

## R-9 -- PA-10 / PA-11 (HIGH-01 cross-page balance-equality lock)

P8-c surfaced that the prior-audit `00_priors.md:819-820` rows describing
PA-10 / PA-11 as `open` with "no test" were stale: live source already
carried `tests/test_services/test_balance_calculator.py:532
def test_52_period_penny_accuracy(` and `:907
def test_idempotent_same_inputs_same_outputs(`, which pin the **single
producer** `calculate_balances` in isolation but do NOT exercise the
cross-page balance-equality invariant HIGH-01 governs. HIGH-01's
load-bearing substance -- "no test renders two balance surfaces and asserts
they agree" -- stood at audit close (the three audit-plan greps returned
zero matches against live `tests/`).

Commit 11 (`test(integration): cross-page balance-equality regression lock
(HIGH-01)`, sha `4674e7e`) closes HIGH-01's load-bearing substance:
`tests/test_integration/test_cross_page_balance_equality.py` plus the PT-01
fixture in `tests/conftest.py` render grid / `/savings` / `/accounts`
checking detail / dashboard / year-end net-worth per-account / calendar for
one (account, period, scenario) tuple with a Projected envelope expense
carrying cleared entries, then assert every surface returns the identical
`Decimal`. A 5-case parameter matrix locks the formula (not just the symptom
#1 hand-computed number), a subtotal-reconciliation assertion locks the
E-25 / Q-10 same-formula invariant per case, and a seam-injection negative
control proves the lock bites when any surface diverges. R-9's only
residual is a `00_priors.md` PA-10 / PA-11 row update from `open` to
"single-producer penny-accuracy + idempotency tests present at
`test_balance_calculator.py:532` / `:907`; cross-page lock added Commit 11
(`tests/test_integration/test_cross_page_balance_equality.py`)"; that is a
documentation update to `00_priors.md`, NOT a money finding, and stays
deferred to the next dedicated audit-doc reconciliation pass per the R-8
precedent.

## R-10 -- PA-08 (LOW-03 carry-forward scenario filter)

P8-d surfaced that the prior-audit `00_priors.md:817` row describing PA-08
as `open` with "missing `scenario_id` filter" was stale: live source at
`app/services/carry_forward_service.py:261-263` already filters
`Transaction.scenario_id == scenario_id,` inside
`_build_carry_forward_context`; the mutating bulk UPDATE at `:405-412`
operates only on rows drawn from that scenario-filtered set; the function
signature takes `scenario_id` and the docstring at `:318` states it
"Prevents [cross-scenario mixing]". The PA-08 risk no longer exists in
live source.

Live source re-verified this session (2026-05-21):
`carry_forward_service.py:261-263` still carries the three-clause filter
exactly as P8-d recorded; no commit between the audit close and Commit 35
has touched that filter. R-10 therefore is closed by **prior remediation**
(predating this audit), not by any commit in the
`docs/audits/financial_calculations/remediation_plan.md` sequence. The
only residual is the same `00_priors.md` PA-08 row update from `open` to
"remediated; filter present at `carry_forward_service.py:262`,
scenario-scoped signature + docstring", parked alongside R-9 for the
dedicated audit-doc reconciliation pass.

## What is not closed here

- **R-1 / R-2 / R-2a / R-3 / R-4 / R-5 / R-6 / R-7 / R-8** remain
  documentation-corrections against prior phase outputs (the 21-vs-20
  DIVERGE count, the F-044 / F-046-SoT citation drifts, etc.) and are not
  affected by Commit 35; they are still surfaced-not-smoothed exactly as
  P8-e recorded them.
- **LOW-04 (Phase-4 D3 row nit)** is closed by Commit 35's edit to
  `04_source_of_truth.md` (the `escrow_components.inflation_rate` row is
  promoted from "AUTHORITATIVE (implicit) ... D2 nit" to a standalone
  AUTHORITATIVE classification line, citing the `loan_features.py:111-115`
  CHECK as its DB-enforced domain).
- **LOW-05 (model comment vs `calculate_gap` NULL semantics)** is closed
  by Commit 35's edit to `app/models/user.py` (the comment now reads
  "NULL = no retirement-tax adjustment applied", matching the code per
  A-26).
- **Q-26 sub-2 (the product-contract question: SHOULD a bracket-based
  fallback exist?)** remains the single Phase-8 carried-open tail, exactly
  as `08_findings.md:3097-3106` and `09_open_questions.md:1841-1879`
  recorded it. A-26 decided the remediation direction (do NOT build a
  fallback); the product-contract decision (whether the feature should
  exist) is a separate Phase-9 / post-audit product question that Commit
  35 explicitly does NOT resolve.

This addendum is documentation-only; no source, test, migration, template,
or JS file is touched by it (the Commit-35 edits to `app/models/user.py`
and `docs/audits/financial_calculations/04_source_of_truth.md` are
recorded in the commit message, not retroactively in this audit doc).
