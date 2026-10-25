> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Phase 5 -- Symptom-driven investigation

Output of Phase 5 (audit-plan section 5; sequenced by `phase5_plan.md`). Each
developer-reported symptom is one subsection carrying every element of
`phase5_plan.md` section 3, under the trust-but-verify contract of section 2:
every hypothesis-tree node cites `file:line` Read at source during this Phase 5
session (Phase 3/4 line citations re-opened, not quoted from memory); every
worked example is recomputed by hand with intermediate Decimal steps; the C2
map and drift register are treated as falsifiable hypotheses, not scaffolding.
Read-only, plan mode: no app run, no code/test/migration edit.

Sessions: P5-a (symptom #1); P5-b (symptoms #2 / #3 / #4, the one
loan-resolver family); P5-c (symptom #5 + cross-symptom synthesis) --
subsections below.

---

## Symptom #1 -- checking balance: ~$160 on the grid vs ~$114.29 on /savings

### Symptom (developer's words)

From `financial_calculation_audit_plan.md:594-597`:

> Projected end balance for the current pay period displays as $160 on the
> grid; checking account balance on `/savings` displays as $114.29. Both
> should be the same number computed from the same inputs.

### Reproduction path

- **Pages:** the grid (`/` -> `grid.index`) balance row for the current pay
  period, vs the `/savings` dashboard checking-account "current balance" tile.
- **Account/period:** the user's default Checking account, the *current* pay
  period (the leftmost grid column; `current_bal = balances.get(current_period.id)`
  on `/savings`).
- **User input that exhibits it:** the current period contains at least one
  *Projected* envelope expense (an expense transaction carrying
  `TransactionEntry` rows) whose debit entries are partly or wholly
  `is_cleared = True` (the user has marked some line-item purchases as already
  posted to the bank), and/or carries credit entries. With every entry still
  uncleared the two pages agree; the gap appears precisely when cleared-debit
  or credit entry value exists on a Projected expense in that period.

### Hypothesis tree (backward from both displayed numbers; every node Read this session)

Both pages display `balances.get(current_period.id)` from the SAME pure engine
`balance_calculator.calculate_balances` (`balance_calculator.py:35-109`). The
tree walks backward from each displayed number to the first input that differs.

```
[GRID] grid balance row value = balances[current_period.id]
  G1. Produced by balance_calculator.calculate_balances(...)
      call site: grid.py:243-248 (full page) and grid.py:446-451 (HTMX
      _balance_row partial). Both call the same engine.
      inputs:
        - anchor_balance  = account.current_anchor_balance if account
                            else Decimal("0.00")          grid.py:238 / :443
        - anchor_period_id= account.current_anchor_period_id if account
                            else current_period.id          grid.py:239-241 / :444
        - periods         = all_periods (anchor-forward)     grid.py:210 / :426
        - transactions    = all_transactions, query at
                            grid.py:226-234 / :436-441
                            *** selectinload(Transaction.entries) ***
                            grid.py:229  AND  grid.py:438   (BOTH grid paths
                            eager-load entries)
  G2. Engine: current period == anchor period -> _sum_remaining(period_txns)
      balance_calculator.py:72-75 ; else _sum_all  :77-80
      running_balance = anchor_balance + income - expenses   :75 / :80
  G3. _sum_remaining (:389-419) / _sum_all (:422-451):
        - Projected-only gate: status_id != projected_id -> continue
          :411-412 / :443-444
        - income  += txn.effective_amount    :415 / :447
        - expense += _entry_aware_amount(txn) :417 / :449
  G4. _entry_aware_amount(txn)  :292-386
        - 'entries' in txn.__dict__ ?  (selectinload populated it) -> TRUE
          for grid (G1 loaded entries)              :353-354
        - entries non-empty, status_id == projected_id            :356-366
        - three-bucket partition over entries:                    :368-378
            cleared_debit   = sum(amount where not is_credit and     is_cleared)
            uncleared_debit = sum(amount where not is_credit and not is_cleared)
            sum_credit      = sum(amount where is_credit)
        - return max(estimated_amount - cleared_debit - sum_credit,
                     uncleared_debit)                              :383-386

[SAVINGS] /savings checking current balance = balances.get(current_period.id)
  S1. savings_dashboard_service.py:352  current_bal = balances.get(current_period.id)
  S2. balances from balance_calculator.calculate_balances(...)
      call site: savings_dashboard_service.py:343-348 (no-interest checking)
      / :335-341 (interest variant; same engine, F-002/F-009 axis unchanged)
      gated by `if anchor_period_id:`  :333
      inputs:
        - anchor_balance  = acct.current_anchor_balance or Decimal("0.00")
                                                       savings_dashboard_service.py:325
        - anchor_period_id= acct.current_anchor_period_id or
                            (current_period.id if current_period else None)  :326-328
        - periods         = all_periods                :87 / :338 / :346
        - transactions    = acct_transactions, filtered :320-323
                            from the query at :92-100
                            *** NO selectinload(entries) ***  :92-100
  S3. Engine: identical to G2/G3 (same module, same functions)
  S4. _entry_aware_amount(txn)  :292-386
        - 'entries' in txn.__dict__ ?  -> FALSE (S2 did NOT eager-load
          entries; lazy='select' relationship absent from __dict__)  :353
        - return txn.effective_amount                              :354
  S5. Transaction.effective_amount  transaction.py:222-245
        - not is_deleted (query already excludes is_deleted)
        - status not excludes_from_balance (Projected) -> not 0     :240-241
        - actual_amount is None (still Projected) ->
          return estimated_amount                                   :245
```

The two backward walks are identical at every node -- same call into the same
engine, same anchor read result (see branch linkage below), same `all_periods`,
same baseline scenario (`grid.py:177`, `savings_dashboard_service.py:85`), same
`is_deleted` query gate (`grid.py:222`, `savings_dashboard_service.py:97`), same
Projected-only status gate inside the engine -- until **G4 vs S4**. The single
divergent input is whether the consuming query issued
`selectinload(Transaction.entries)`. Grid did (`grid.py:229` and `:438`);
`/savings` did not (`savings_dashboard_service.py:92-100`). That one difference
routes the IDENTICAL Projected envelope expense down two different return
statements inside `_entry_aware_amount`: the entry formula
(`:383-386`) on the grid, the raw `effective_amount` = `estimated_amount`
(`:354` -> `transaction.py:245`) on `/savings`.

### Per-branch finding linkage

- **G4 vs S4 -- entries-load expense divergence (THE driver).** Governed by
  **F-009** (`03_consistency.md:649-726`, the symptom-#1 dedicated finding;
  verdict DIVERGE / SILENT_DRIFT, sole pinned dimension =
  `selectinload(Transaction.entries)` present `grid.py:229` / absent
  `savings_dashboard_service.py:92-100`). Also **F-002** Pair B
  (`03_consistency.md:214-279`, same mechanism) and **F-003** checking flavor
  (`:283-338`). Resolved intent: **E-25** (`00_priors.md:276-284`, one
  producer, one entry-aware base shared with the balance calculator) and the
  E-04 unlabeled-divergence prior. Re-verified at source this session:
  `_entry_aware_amount` short-circuit `balance_calculator.py:353-354` vs entry
  formula `:383-386`; `selectinload` asymmetry confirmed at `grid.py:229`,
  `grid.py:438`, and the bare query `savings_dashboard_service.py:92-100`.

- **Anchor-handling branch (G1 `if account else` vs S2 `or`).** Governed by
  the **F-001/F-003 anchor-None SCOPE axis**, Q-16/Q-20 -> **E-19**
  (`00_priors.md:198-213`; A-16 `09_open_questions.md:829-839`, A-20 `:1059-1069`
  -- the `current_anchor_period_id IS NULL` state is eliminated, not
  interpreted). **NOT causal for the reported $160/$114.29.** When
  `account.current_anchor_period_id` is set (which the "current pay period with
  a set checking balance" symptom presupposes), `grid.py:238-241` yields
  `(current_anchor_balance, current_anchor_period_id)` and
  `savings_dashboard_service.py:325-328` yields the identical pair (`or` only
  fires when the column is falsy). The two anchor expressions diverge ONLY in
  the NULL-period case (Q-16/Q-20), which produces blank/omit/$0 artifacts --
  not the specific $160 vs $114.29 values. This branch is a real finding but a
  *different* one; it is recorded as governed-but-not-causal for symptom #1's
  numbers.

- **Grid same-page facet (Pair C): grid subtotal vs grid balance row.**
  `grid.py:263-279` computes the period subtotal with raw `txn.effective_amount`
  (`:272` income, `:274` expense) -- NOT `_entry_aware_amount` -- even though
  entries ARE loaded on that same request (`grid.py:229`). Governed by **F-002
  Pair C / F-004**, resolved intent **E-25** (`00_priors.md:278-284`: the
  subtotal must share `_entry_aware_amount` with the balance calculator). This
  is a *second, same-page* manifestation of the identical root (one concept,
  two expense bases). It is not the cross-page $160/$114.29 driver but shares
  F-009/E-25's root cause and is folded into the remediation direction.

- **Income path (G3/S3 `txn.effective_amount`).** Identical both sides
  (`balance_calculator.py:415` and `:447` on both pages; no entries dependence
  for income). **AGREE -- innocent, no finding, no re-investigation.**

- **Status filter / scenario / is_deleted / period scope / quantization.**
  All AGREE (cited above). No NO-FINDING branch requires
  `RE-INVESTIGATE`: every divergent node maps to an existing Phase-3 finding,
  and every AGREE node was independently confirmed at source this session.
  Phase 3's completeness on symptom #1 is therefore confirmed, not patched.

- **W-277 -- calendar month-end, the SAME defect on a NEW consuming page.**
  `calendar_service._compute_month_end_balance` (`calendar_service.py:435-489`)
  builds its transaction set at `:471-480` with **NO
  `selectinload(Transaction.entries)`** (Read in full this session; the
  `.filter(...).all()` at `:472-480` carries no `.options(...)`), then calls
  the same `balance_calculator.calculate_balances` at `:482-487`. By the exact
  G4-vs-S4 mechanism, the calendar month-end balance takes the
  `effective_amount` branch (`balance_calculator.py:354`) and DIVERGES from the
  grid identically to `/savings`. This is **F-003/F-009's entries-load instance
  on a third page** -- governed by E-25 (entries-aware single producer) and
  E-04. W-277 additionally carries two calendar-only axes that are NOT part of
  symptom #1's number but are recorded for completeness:
  (a) **period-selection** -- `:461-466` picks the LAST period with
  `end_date <= last_day` (`for p in all_periods: if p.end_date <= last_day:
  target_period = p`), an up-to-~13-day undershoot of the true calendar
  month-end, governed by **E-27** (`00_priors.md:298-310`); and
  (b) **anchor-None short-circuit** -- `:449-450` `if
  account.current_anchor_period_id is None: return Decimal("0")`, subsumed by
  **E-19** (state eliminated). The entries-load half is the same defect as
  symptom #1.

### Worked example (hand-recomputed this session, intermediate Decimal steps)

Construct the minimal reproduction: current pay period == the checking account's
anchor period (so `_sum_remaining` applies, `balance_calculator.py:72-75`),
baseline scenario, no income in the period (income is identical both paths and
cancels), and exactly one Projected envelope expense.

Inputs (all `Decimal`, constructed from strings per coding-standards):

- Checking real anchor balance `A = Decimal("614.29")`
  (`account.current_anchor_balance`).
- One Projected expense "Groceries": `estimated_amount = Decimal("500.00")`,
  `actual_amount = None`, `status = Projected`.
- Its `TransactionEntry` rows: three *cleared debit* purchases already posted
  to the bank, `is_credit = False`, `is_cleared = True`, amounts
  `Decimal("20.00")`, `Decimal("15.71")`, `Decimal("10.00")`. No credit
  entries, no uncleared entries.

  `cleared_debit   = Decimal("20.00") + Decimal("15.71") + Decimal("10.00")`
  `                = Decimal("45.71")`
  `uncleared_debit = Decimal("0")`
  `sum_credit      = Decimal("0")`

**Grid path** (entries eager-loaded, `grid.py:229`):

`_entry_aware_amount` reaches the formula (`balance_calculator.py:383-386`):

```
expense_grid = max(estimated_amount - cleared_debit - sum_credit, uncleared_debit)
             = max(Decimal("500.00") - Decimal("45.71") - Decimal("0"),
                   Decimal("0"))
             = max(Decimal("454.29"), Decimal("0"))
             = Decimal("454.29")
```

`_sum_remaining` -> `income = Decimal("0.00")`, `expenses = Decimal("454.29")`
(`balance_calculator.py:403-419`).
Anchor-period running balance (`:75`):

```
running_balance = A + income - expenses
                = Decimal("614.29") + Decimal("0.00") - Decimal("454.29")
                = Decimal("160.00")
```

`balances[current_period.id] = Decimal("160.00")` -> **grid shows $160.00.**

**/savings path** (entries NOT loaded, `savings_dashboard_service.py:92-100`):

`_entry_aware_amount` short-circuits at `'entries' not in txn.__dict__`
(`balance_calculator.py:353-354`) and returns `txn.effective_amount`.
`effective_amount` (`transaction.py:238-245`): not deleted, Projected status is
not `excludes_from_balance`, `actual_amount is None` -> returns
`estimated_amount = Decimal("500.00")`.

`_sum_remaining` -> `income = Decimal("0.00")`, `expenses = Decimal("500.00")`.
Anchor-period running balance (`:75`):

```
running_balance = A + income - expenses
                = Decimal("614.29") + Decimal("0.00") - Decimal("500.00")
                = Decimal("114.29")
```

`current_bal = balances.get(current_period.id) = Decimal("114.29")`
(`savings_dashboard_service.py:352`) -> **/savings shows $114.29.**

**The gap:**

```
gap = Decimal("160.00") - Decimal("114.29") = Decimal("45.71")
    = cleared_debit
    = sum over the period's Projected envelope expenses of
      [ estimated_amount
        - max(estimated_amount - cleared_debit - sum_credit, uncleared_debit) ]
```

Interpretation: the $45.71 of grocery purchases is *already cleared in the
bank*, therefore already reflected in the real anchor balance `A = $614.29`.
The grid loads the entries, sees those debits cleared, and holds back only the
unreconciled $454.29. `/savings` never loads the entries, cannot tell the
purchases cleared, and holds back the full $500.00 estimate -- subtracting the
$45.71 a second time (once implicitly in the anchor, once in the projection).
`/savings` is the wrong number; the grid is correct. The sign matches the
report (grid > /savings, $160 > $114.29) and the mechanism is pinned to the
single `selectinload` input difference, not the engine, anchor, status filter,
scenario, or period scope.

### Best-evidence root cause

**F-009 is the precise root cause of symptom #1**, with **W-277** the same
defect surfacing on the calendar month-end page. Stated as a cited hypothesis:
a user-facing checking balance silently changes value depending on whether the
*consuming query* happened to issue `selectinload(Transaction.entries)` --
present at `grid.py:229`/`:438`, absent at
`savings_dashboard_service.py:92-100` and `calendar_service.py:471-480`. The
shared engine `_entry_aware_amount` (`balance_calculator.py:292-386`) was
written to *degrade silently* to `effective_amount` when entries are not loaded
(`:353-354`), which makes the per-expense checking impact a function of an
ORM-eager-loading implementation detail rather than of the data.

This is precisely the one-concept-two-bases inconsistency that **E-25**
(`00_priors.md:276-284`) rejects and the unlabeled cross-page difference that
the E-04 prior rejects. The anchor-None axis (Q-16/Q-20 -> **E-19**,
`00_priors.md:198-213`) is a *separate* finding that does not produce these
specific numbers (it governs blank/omit/$0 artifacts when
`current_anchor_period_id IS NULL`, not the $160 vs $114.29 of a set anchor in
the current period). The calendar period-selection axis (**E-27**,
`00_priors.md:298-310`) is W-277's own additional defect, not part of symptom
#1's number.

**Remediation direction (already fixed by E-25, stated, not deferred):** the
entry-aware checking-impact reduction must not depend on whether an arbitrary
caller eager-loaded `entries`. The canonical balance producer must own that
guarantee -- either the single period-subtotal/balance producer always loads
entries for the accounts it projects, or the entry-aware reduction is performed
inside that one producer which by construction has the entry data -- so grid,
`/savings`, `/accounts`, dashboard, and calendar all receive the IDENTICAL
number from the same base. Concretely against the cited lines: the silent
`'entries' not in txn.__dict__ -> return effective_amount` fallback
(`balance_calculator.py:353-354`) is the drift seam; under E-25 the producer
guarantees loaded entries (or computes the reduction itself), eliminating the
seam rather than adding a `selectinload` to each of the three consumers
(`savings_dashboard_service.py:92-100`, `calendar_service.py:471-480`, and the
grid subtotal at `grid.py:263-279` which must also move off raw
`effective_amount` onto the shared `_entry_aware_amount` base per E-25). E-19
separately removes the anchor-None branch by eliminating the NULL-period state;
E-27 separately fixes the calendar's period selection to the true month-end
date through the same entries-aware "balance as of date" path.

### Independent-vs-inherited note

The independent backward trace from BOTH displayed numbers **CONFIRMS** the C2
map row #1 (`03_consistency.md:6053`: "F-009 + W-277 (new consuming path)") and
**NARROWS** the drift register row #1 (`04_source_of_truth.md:2138`, which
lists "Q-16, **Q-20**" as co-blocking the symptom-#1 explanation thread):

- **Confirms:** the sole pinned dimension is the `selectinload(entries)`
  asymmetry; F-009's worked reconstruction (`03_consistency.md:698-716`) and
  the C2 cell both predicted the $45.71 = `sum(estimated - max(est - cleared -
  credit, uncleared))` gap, which this session's independent hand-recompute
  reproduces exactly. W-277 as "the SAME gap on a NEW page" is confirmed by the
  full Read of `calendar_service.py:435-489` (no `.options(selectinload(...))`
  on the `:472-480` query).
- **Narrows:** the drift register attaches Q-16/Q-20 (anchor-None SCOPE) to
  symptom #1's thread. The independent trace shows the anchor-None axis is
  **real but NOT causal for the reported $160/$114.29**: with
  `current_anchor_period_id` set (the symptom's own premise -- a current-period
  checking balance that displays), the grid `if account else` and `/savings`
  `or` anchor expressions resolve to the identical `(balance, period_id)` pair,
  so that axis cannot produce the reported values. The SILENT_DRIFT
  entries-load axis is the sole driver; the SCOPE anchor-None axis is a
  distinct finding (now E-19-resolved) that belongs to symptom #5's family, not
  to the numbers reported here. This is a **narrowing of scope, not a
  contradiction** of any prior verdict: F-009/F-002/F-003 verdicts and their
  proven divergence facts are unchanged.

### Verification plan (documentation only -- nothing run, no code modified)

1. **Confirm the source asymmetry persists (code read).** Re-open and confirm:
   `selectinload(Transaction.entries)` at `grid.py:229` and `grid.py:438`;
   absent on the query at `savings_dashboard_service.py:92-100`; absent on the
   query at `calendar_service.py:471-480`. Confirm `_entry_aware_amount`
   short-circuit still at `balance_calculator.py:353-354` and formula at
   `:383-386`.
2. **Reconstruct the developer's actual gap (DB query, read-only).** For the
   developer's checking account, current pay period, baseline scenario: select
   all Projected expense transactions and, for each, its `TransactionEntry`
   rows (`amount`, `is_credit`, `is_cleared`) and its `estimated_amount`.
   Compute, per transaction,
   `estimated_amount - max(estimated_amount - cleared_debit - sum_credit,
   uncleared_debit)`; sum over the period. Assert the sum equals the observed
   grid-minus-`/savings` gap ($45.71 in the report). If it does not, the
   entries-load axis is not the sole driver and the tree's AGREE nodes (anchor,
   scenario, period scope) must be re-investigated -- this is the falsification
   test.
3. **Cross-page hand check.** With the same anchor `A` and the same transaction
   set, hand-evaluate `calculate_balances` twice: once with entries present
   (entry formula), once without (effective_amount). Confirm
   `grid_balance - savings_balance` equals the period sum of
   `cleared_debit + sum_credit` value already reflected in the anchor.
4. **W-277 confirmation.** For the same account and the month containing the
   current period, hand-evaluate `_compute_month_end_balance`: confirm (a) it
   takes the `effective_amount` branch (entries unloaded) and therefore matches
   `/savings`, not the grid; and (b) `target_period` selected at
   `calendar_service.py:461-466` may end up to ~13 days before the true
   calendar month-end, an independent E-27 undershoot layered on top of the
   entries-load gap.
5. **Equivalence after remediation (future regression target, documented).**
   Once E-25's single entries-aware producer is in place, the assertion to
   encode is: for any account/period/scenario,
   `grid.balances[p] == savings.balances[p] == calendar_month_end(p_containing
   month-end) == accounts.balances[p]` -- the E-04 invariant -- with the entry
   formula applied uniformly.

---

## Symptom #2 -- mortgage payment $1911.54 / $1914.34 / $1912.94, then $1910.95 after editing current principal

### Symptom (developer's words)

From `financial_calculation_audit_plan.md:598-601`:

> Mortgage payment amount on the amortization schedule has been observed at
> $1911.54, $1914.34, and $1912.94 on different views or different sessions.
> Updating the current principal in loan parameters on `/accounts/3/loan`
> changes it to $1910.95.

### Reproduction path

- **Page:** `/accounts/3/loan` (`loan.dashboard`). The bold "Monthly P&I" card
  (`loan/dashboard.html:129` = `summary.monthly_payment`) vs the first
  projected row's Payment column in the amortization schedule on the SAME page
  (`loan/_schedule.html:55` = `row.payment`), plus the same figure re-read in a
  later calendar-month session.
- **Account/period:** account 3, an ARM mortgage (`LoanParams.is_arm = True`),
  viewed inside its fixed-rate window, with the stored `current_principal`
  never settle-updated (symptom #3 premise).
- **User input that exhibits it:** none required beyond *time passing* and
  *looking at two surfaces*; the fourth value appears only when the user types
  a new value into the "Current Principal" input
  (`loan/dashboard.html:160-162`) and POSTs `loan.update_params`.

### Hypothesis tree (backward from each displayed number; every node Read this session)

```
[CARD] dashboard "Monthly P&I"  loan/dashboard.html:129  summary.monthly_payment
  C1. summary = proj.summary ; proj = get_loan_projection(params, payments,
      rate_changes)                                   loan.py:429-432
  C2. monthly_payment (ARM branch)                    amortization_engine.py:950-954
        if is_arm and remaining > 0:
          monthly_payment = calculate_monthly_payment(current_principal,
                                                      rate, remaining)
      inputs:
        - current_principal = Decimal(str(params.current_principal))  :913  (STORED)
        - rate              = Decimal(str(params.interest_rate))      :914  (STORED)
        - remaining         = calculate_remaining_months(
                                params.origination_date,
                                params.term_months)    :908-910
  C3. calculate_remaining_months  :128-142
        months_elapsed = (as_of.year-orig.year)*12 + (as_of.month-orig.month)
        return max(0, term_months - months_elapsed)    :138-142
        *** as_of defaults to date.today() :136-137 -> n shrinks by 1 / month ***
  C4. calculate_monthly_payment(P, r, n)  :178-197
        i = r/12 ; f = (1+i)**n ; M = (P * i*f/(f-1)).quantize(HALF_UP)  :194-197

[ROW] schedule first projected Payment  loan/_schedule.html:55  row.payment
  R1. amortization_schedule = proj.schedule              loan.py:547
  R2. generate_schedule(orig_principal, rate, params.term_months, ...,
        original_principal = None (ARM, :920), term_months,
        anchor_balance = current_principal (:926),
        anchor_date = date.today() (:927))               amortization_engine.py:932-942
  R3. using_contractual = original_principal is not None and ... -> FALSE (ARM) :430-434
      max_months = remaining_months = params.term_months = T  :452-455
  R4. loop month_num=1..max_months ; pay_date = month (orig+month_num) @
      payment_day  :458-480
  R5. anchor reset at FIRST row with pay_date > anchor_date(=today)  :486-493
        balance       = anchor_balance = STORED current_principal  :488
        months_left   = max_months - month_num + 1                 :490
        monthly_payment = calculate_monthly_payment(balance,
                            current_annual_rate, months_left)       :491-493
      *** n here = T - month_num + 1, month_num = first post-today index ***

[STRATEGY] /strategy minimum payment  debt_strategy.py:127-129  (same loan)
  T1. real_principal: _compute_real_principal -> ARM returns stored principal
      verbatim  debt_strategy.py:169-173
  T2. remaining = calculate_remaining_months(orig, term)  debt_strategy.py:111-113
  T3. minimum_payment = calculate_monthly_payment(real_principal, rate,
      remaining)  debt_strategy.py:127-129   -> SAME (P,r,n) triple as the CARD
      for an ARM

[SAVINGS] /savings debt-card P&I  savings_dashboard_service.py:846
  V1. monthly_pi = ad["monthly_payment"]  :846  <- :433 ad["monthly_payment"]
      = monthly  :367 = proj.summary.monthly_payment from
      get_loan_projection(acct_loan_params, ...)  :362-367  -> SAME site-7
      scalar as the CARD
```

The four backward walks share one engine formula
(`calculate_monthly_payment`, `:178-197`) and, for an ARM, the SAME stored
`P` (`:913`) and SAME stored `r` (`:914`). They diverge in exactly one input:
`n`. The CARD / STRATEGY / SAVINGS scalar uses
`n_card = calculate_remaining_months = T - months_elapsed` (`:138-142`). The
schedule ROW uses `n_row = max_months - month_num + 1`
(`amortization_engine.py:490`) where `max_months = params.term_months = T`
(`:455`, because `using_contractual` is False for ARM, `:430-434` with
`original = None` `:920`) and `month_num` is the first loop index whose
`pay_date > today` (`:486-487`). `month_num` is `months_elapsed` or
`months_elapsed + 1` depending on whether `payment_day` falls before or after
`today`'s day-of-month, so `n_row` is `T - months_elapsed + 1` or
`T - months_elapsed` -- i.e. `n_card` or `n_card + 1`. Two values for the same
loan on the same page on the same day. Across sessions a calendar month apart,
`n_card` itself shrinks by 1 (C3), so the CARD alone yields a different value
each month -- the symptom-#4 mechanism, here surfacing as symptom #2's
"different sessions."

### Per-branch finding linkage

- **C2/C3 vs R5 -- the `n`-source divergence (the same-day driver).** Governed
  by **F-013** (`03_consistency.md:1009-1148`, the 16-site
  incompatible-`(P,r,n)`-triple finding; verdict DIVERGE, SILENT_DRIFT for the
  site-7-vs-3 `n` axis). Re-verified at source this session: site-7 `n` at
  `amortization_engine.py:908-910` -> `:138-142`; site-3 `n` at `:490` with
  `max_months` at `:455` and the anchor gate at `:486-493`.
- **C3 temporal axis -- `n_card` shrinks monthly (the cross-session driver).**
  Governed by **F-026** (`03_consistency.md:1936-2037`, symptom-#4 finding) --
  symptom #2's "different sessions" face is literally symptom #4 read off the
  Monthly-P&I card. Same root column.
- **After-edit drop to $1910.95.** Governed by **F-014 / F-015**
  (`03_consistency.md:1150-1297`): `update_params` `setattr(params, field,
  value)` (`loan.py:672-674`, `"current_principal" in _PARAM_FIELDS` `:669`)
  is the *sole* post-creation writer of the stored column; site-7 immediately
  re-amortizes the smaller `P` -> lower `M`. Re-verified at source this
  session (`loan.py:668-674`).
- **STRATEGY / SAVINGS scalar = CARD scalar for ARM.** `debt_strategy.py:172-173`
  returns stored principal; `:111-113,127-129` uses `calculate_remaining_months`;
  `savings_dashboard_service.py:362-367,846` reuses `proj.summary.monthly_payment`.
  AGREE with the CARD for an ARM -- no separate divergence, no
  `RE-INVESTIGATE` (F-013's site-16 third value applies only to *fixed* loans;
  account 3 is the mortgage/ARM).
- **Rate axis.** Inside a 5/5 ARM fixed window there are no RateHistory rows,
  so `_find_applicable_rate` (`amortization_engine.py:498-514`) never fires and
  the stored `r` (`:914`) equals the schedule's rate. **AGREE in-window -- not
  the driver** (Q-23 sub-1 / A-23; the rate is the symptom only outside the
  window, F-013 site-4). No `RE-INVESTIGATE`.
- **Quantization.** Single `quantize(TWO_PLACES, ROUND_HALF_UP)` at the
  producer (`:197`). AGREE -- A-01-clean, not the driver.

### Worked example (hand-recomputed this session, intermediate Decimal steps)

The developer's real account-3 parameters are not in the codebase and the app
is not run (contract item 6); per `04_source_of_truth.md:1142-1158` the
**mechanism, sign, and few-dollar shape** are the pinned facts, the absolute
cents are illustrative. Representative ARM matching the symptom's order of
magnitude:

- `original_principal = Decimal("300000.00")`
- STORED `current_principal = Decimal("300000.00")` (never settle-updated --
  symptom #3 / `loan.py:674` sole writer)
- `interest_rate = Decimal("0.06000")` -> monthly `i = 0.06/12 = Decimal("0.005")`
- `term_months = 360`; `is_arm = True`; `arm_first_adjustment_months = 60`
  (fixed window months 1-60); no RateHistory inside the window
- `origination_date = 2022-05-01`; `today = 2026-05-18` (session date)

`months_elapsed = (2026-2022)*12 + (5-5) = 48` (`:138-141`). Inside the
60-month fixed window.

Engine annuity (`:194-197`), written as `M = P*i / (1 - (1+i)^(-n))`:

`(1.005)^312` (the engine's `(1+monthly_rate)**remaining_months`):
`ln(1.005) = 0.004987542`; `* 312 = 1.556113`; `e^1.556113 = 4.740373`.
`(1.005)^(-312) = 1 / 4.740373 = 0.210955`; `1 - 0.210955 = 0.789045`.

**Value A -- "Monthly P&I" card** (site 7, `n_card = T - months_elapsed =
360 - 48 = 312`, `amortization_engine.py:952-954`):

```
M_A = (P * i) / (1 - (1.005)^-312)
    = (Decimal("300000.00") * Decimal("0.005")) / Decimal("0.789045")
    = Decimal("1500.00") / Decimal("0.789045")
    = Decimal("1901.03")          (quantize HALF_UP, :197)
```

**Value B -- first projected schedule row** (site 3). With `payment_day = 25`,
`pay_date(month_num=48) = 2026-05-25 > today 2026-05-18`, so the anchor fires
at `month_num = 48` (`:486-487`); `max_months = term_months = 360` (`:455`);
`months_left = 360 - 48 + 1 = 313` (`:490`):

`(1.005)^313 = 4.740373 * 1.005 = 4.764075`; `(1.005)^-313 = 0.209906`;
`1 - 0.209906 = 0.790094`.

```
M_B = Decimal("1500.00") / Decimal("0.790094") = Decimal("1898.50")
```

Same loan, same page, same day: card $1901.03 vs first schedule row $1898.50
-- a **$2.53** spread from the single `n` off-by-one (313 vs 312), exactly
the developer's "$1911.54 vs $1912.94 on different views."

**Value C -- the card one calendar month later** (`months_elapsed = 49`,
`n_card = 360 - 49 = 311`, no edit, no rate change):

`(1.005)^311 = 4.740373 / 1.005 = 4.716789`; `(1.005)^-311 = 0.212008`;
`1 - 0.212008 = 0.787992`.

```
M_C = Decimal("1500.00") / Decimal("0.787992") = Decimal("1903.57")
```

The card alone moves $1901.03 -> $1903.57 (**+$2.54**) across two sessions a
month apart. The developer's non-monotone trace ($1914.34 then down to
$1912.94) is the same-day cross-site spread (Value A vs Value B, sign can go
either way by day alignment) **superimposed on** the monotone month-over-month
creep (Value A -> Value C) -- precisely the reconciliation
`04_source_of_truth.md:1150-1154` predicts.

**Value D -- after editing current principal.** The user types their bank
statement balance, e.g. `Decimal("298000.00")`, into
`loan/dashboard.html:160-162`; `update_params` `setattr(params,
"current_principal", Decimal("298000.00"))` (`loan.py:672-674`). Site 7
recomputes with `P_new`, `n_card = 312`:

```
M_D = (Decimal("298000.00") * Decimal("0.005")) / Decimal("0.789045")
    = Decimal("1490.00") / Decimal("0.789045")
    = Decimal("1888.36")
```

Lower than A/B/C. The exact cent depends on what the user typed; the **pinned
fact is the sign**: a smaller stored `P` -> strictly lower `M` (monotone in
`P`), matching the developer's $1910.95 being the lowest of the four
observations.

### Best-evidence root cause

Symptom #2 is **F-013 (the cross-site `n` axis) layered on F-026 (the monthly
`n_card` creep), both rooted in the un-maintained stored `current_principal`
read at `amortization_engine.py:913` and re-amortized over a calendar-shrinking
`calculate_remaining_months` (`:908-910` -> `:138-142`)**. Stated as a cited
hypothesis: there is no single resolver that owns "this loan's monthly
payment"; instead `calculate_monthly_payment` (`:178-197`) is fed from >=4
surfaces that each assemble their own `(P, r, n)` -- the scalar branch
(`:952-954`, `n` = calendar-remaining), the schedule anchor branch (`:491-493`,
`n` = loop-relative `max_months - month_num + 1`), debt-strategy
(`debt_strategy.py:127-129`), and the savings card
(`savings_dashboard_service.py:846`). For an ARM the only inter-surface
difference is `n`, and `n` is unstable both across surfaces (off-by-one,
day-alignment dependent) and across time (shrinks monthly while `P` is frozen
because settle never writes the column -- symptom #3).

**Governing intent: E-18** (`00_priors.md:184-197`; resolves Q-17/A-17,
Q-22/A-22, Q-23/A-23 as one stored-mirror-retirement policy). E-18 fixes the
remediation, stated not deferred: a *single resolver* derives the triple
(balance, monthly_payment, schedule) from the loan's event stream (origination
terms, confirmed payments, rate-change entries, dated user-verified anchor
events); every surface -- the card, the schedule, debt-strategy, the savings
PITI, refinance prefill -- reads only that resolver. The stored
`current_principal` and `interest_rate` are retired as authoritative or
hand-edited scalars (A-22/A-23); the "edit current principal" control becomes
an append-only dated anchor event, not a scalar overwrite (so Value D stops
being a silent global recompute). With one resolver replaying confirmed
payments forward from the latest anchor and honoring
`arm_first_adjustment_months`, there is no second `n`, no frozen `P`, and the
in-window payment is constant by construction -- #2's same-day spread and
cross-session creep both vanish.

### Independent-vs-inherited note

**CONFIRMS with one narrowing.** The C2 map row #2
(`03_consistency.md:6054`) and drift register row #2
(`04_source_of_truth.md:2139`) attribute #2 to F-013 + the frozen stored
principal; the independent trace lands on exactly that. **Narrowing of F-013's
worked reconstruction** (`03_consistency.md:1116-1119`): that reconstruction
states site-3 `n ~= remaining + 1` ("off by ~1", citing
`max_months - month_num + 1` with `month_num ~= e + 1`). The independent
trace this session proves `max_months = params.term_months = T` (not
`T - e`; `:455` with `using_contractual` False for ARM via `original = None`
at `:920`), and that `month_num` at the first post-today row is
`months_elapsed` **or** `months_elapsed + 1` strictly by the
`payment_day`-vs-`today.day` comparison at `:486-487` -- so site-3 `n` equals
site-7 `n` **or** `site-7 n + 1` (gap is 0 or 1, day-alignment dependent), not
invariably +1. The F-013 verdict (DIVERGE) and root attribution are unchanged;
only the off-by-one's determinacy is sharpened.

### Verification plan (documentation only -- nothing run, no code modified)

1. **Reconstruct account 3's ledger (read-only DB).** Pull `LoanParams` for
   account 3 (`original_principal`, `current_principal`, `interest_rate`,
   `term_months`, `origination_date`, `payment_day`, `is_arm`,
   `arm_first_adjustment_months`) and confirm `is_arm = True` and
   `today` inside the fixed window.
2. **Hand-evaluate the four sites** with those exact values:
   `M_card = calculate_monthly_payment(current_principal, interest_rate,
   T - months_elapsed)`; `M_row` with `n = T - month_num + 1` for the first
   `pay_date > today`; confirm `|M_card - M_row|` and the month-to-month
   `M_card` delta together span the observed $1911.54 / $1914.34 / $1912.94.
   If they do not, the `n`-axis is not the sole same-loan driver and the
   AGREE rate/quantization nodes must be re-investigated (falsification test).
3. **After-edit check.** Confirm `update_params` (`loan.py:672-674`) wrote the
   stored column and that site-7 with the new `P` reproduces $1910.95's sign
   (strictly lower than the pre-edit card).
4. **Equivalence after remediation (regression target, documented).** Once the
   single E-18 resolver is in place: for account 3 on any date,
   `card.monthly_payment == schedule.first_projected_row.payment ==
   strategy.minimum_payment == savings.debt_card_PI`, and that value is
   constant for every date inside `[origination, origination +
   arm_first_adjustment_months)`.

---

## Symptom #3 -- current principal does not update as transfers to the mortgage settle

### Symptom (developer's words)

From `financial_calculation_audit_plan.md:602-605`:

> The current principal on the mortgage account does not appear to update as
> transfers to the mortgage account are made. The developer expects confirmed
> transfers (settled shadow income on the loan account) to reduce the stored
> or computed real principal.

### Reproduction path

- **Page:** `/accounts/3/loan` "Current Principal" card
  (`loan/dashboard.html:104` = `params.current_principal`).
- **Account:** account 3, the ARM mortgage.
- **User input that exhibits it:** the user makes the monthly PITI transfer to
  the mortgage; the loan-side shadow income moves to a settled status (Transfer
  Invariant 1). The card does not move. It moves only when the user manually
  edits the field.

### Hypothesis tree (backward from the card; every node Read this session)

```
[CARD] "Current Principal"  loan/dashboard.html:104
  = ${{ "{:,.2f}".format(params.current_principal|float) }}   (STORED column, raw)
  P1. params passed by route: render_template("loan/dashboard.html",
      ..., params=params, ...)        loan.py:553-557
      *** proj is computed loan.py:429-431 but NOT wired to the card ***
  P2. params.current_principal -> budget.loan_params.current_principal
      Numeric(12,2) NOT NULL   loan_params.py:54  (CHECK >= 0 :31-34)

[WRITERS] who can change budget.loan_params.current_principal?
  W1. Creation: LoanParams(account_id=..., **data)   loan.py:622  (create_params)
  W2. The ONLY post-creation writer: update_params
        data = _update_schema.load(request.form)       loan.py:653
        _PARAM_FIELDS = { "current_principal", ... }    loan.py:668-671
        for field, value in data.items():
          if field in _PARAM_FIELDS: setattr(params, field, value)  :672-674
      -> a human typing the "Current Principal" input
         (loan/dashboard.html:160-162) and POSTing the form.
  W3. Settle path: NONE.  (grep this session, below)

[SETTLE] transfer-to-loan settles -> does anything reduce the column?
  S1. Confirmed transfer settles via transfer_service.update_transfer
      :497-502 -- propagates new_status_id to xfer + expense_shadow +
      income_shadow ONLY.  No loan_params access; module does not import
      LoanParams (grep S-a).
  S2. transaction_service.settle_from_entries :38-168 -- writes txn.status_id
      /paid_at/actual_amount only; rejects transfer shadows outright
      (txn.transfer_id is not None -> ValidationError, per
      04_source_of_truth.md:534-538).  No principal write.
  S3. get_loan_projection consulted by NO settle path; even if it were:
        ARM: cur_balance = current_principal   amortization_engine.py:977-978
             (STORED verbatim; confirmed schedule rows NOT consulted)
        fixed: cur_balance walks reversed(schedule) to last is_confirmed row
             :980-984 (DOES move) -- but the card renders STORED, not proj
             (P1), so even fixed does not move on the card.
```

Independent grep proof, re-run this session (not inherited from F-014):

```
$ grep -rEn "\.current_principal\s*=[^=]" app/ scripts/ --include='*.py'
  (zero matches)
$ grep -rn "import LoanParams|loan_params import" app/ --include=*.py \
    | grep -v app/models/loan_params.py
  app/routes/loan.py, app/routes/debt_strategy.py, app/routes/accounts.py,
  app/services/loan_payment_service.py,
  app/services/year_end_summary_service.py,
  app/services/savings_dashboard_service.py        (6 consumers; no settle module)
$ for f in transfer_service transaction_service state_machine entry_service \
    credit_workflow entry_credit_workflow carry_forward_service \
    recurrence_engine transfer_recurrence routes/transactions \
    routes/transfers routes/dashboard: grep "LoanParams|loan_params|
    current_principal"  ->  every one returns NONE
```

The 12 settle / status-transition modules neither import `LoanParams` nor
reference `current_principal`. There is no attribute write to the column
anywhere in `app/` or `scripts/`. The sole post-creation writer is the manual
`update_params` form.

### Per-branch finding linkage

- **W3 / S1-S2 -- zero settle-writer.** Governed by **F-014**
  (`03_consistency.md:1150-1249`, symptom-#3 finding; verdict DIVERGE,
  SOURCE_DRIFT). Independently re-proven at source this session by the three
  greps above and full reads of the writer set.
- **S3 ARM branch -- stored verbatim.** Governed by **F-015**
  (`03_consistency.md:1253-1297`) and the A-04 dual policy: ARM
  `cur_balance = current_principal` (`amortization_engine.py:977-978`). Even
  the engine never reduces the ARM value from confirmed payments.
- **P1 -- card renders STORED regardless of loan type.** Governed by **F-016**
  (`03_consistency.md:1301-1356`, verdict UNKNOWN -> resolved by E-18). Route
  passes `params=params` (`loan.py:553-557`); `proj` (`:429-431`) is not wired
  to the card. Confirmed at source.
- **S3 fixed branch vs `_compute_real_principal`.** Two independent confirmed-
  payment replays (`amortization_engine.py:980-984` vs
  `debt_strategy.py:181-195`, the latter RAW / no escrow subtraction). Governed
  by **F-014 / F-016** SCOPE axis. Not the ARM symptom (account 3 is ARM) but
  recorded: even the fixed "real principal" is computed two disagreeing ways.
- No `NO-FINDING -> RE-INVESTIGATE`: every node maps to F-014/F-015/F-016 and
  every AGREE/absence node was independently grep- or read-confirmed this
  session.

### Worked example (hand-recomputed this session)

ARM mortgage, account 3. STORED `current_principal = Decimal("300000.00")`,
`is_arm = True`. Monthly PITI transfer = `Decimal("1888.36")` P&I +
`Decimal("400.00")` escrow = `Decimal("2288.36")` (figures continuous with
symptom #2's Value D loan).

| Event | What runs | `params.current_principal` | Card (`dashboard.html:104`) |
|---|---|---|---|
| Transfer 1 settles | `transfer_service.update_transfer:497-502` sets status on xfer + 2 shadows; no `LoanParams` import | `300000.00` | **$300,000.00** |
| `get_payment_history` now returns 1 confirmed `PaymentRecord` | -- | `300000.00` | **$300,000.00** |
| Loan dashboard reload | `get_loan_projection`: ARM -> `cur_balance = current_principal` (`:977-978`); confirmed rows NOT consulted | `300000.00` | **$300,000.00** |
| Transfers 2, 3, 4 settle | same as above, x3 | `300000.00` | **$300,000.00** |
| User types `298000.00`, POSTs `update_params` | `setattr(params,"current_principal",Decimal("298000.00"))` `loan.py:674` | `298000.00` | **$298,000.00** |

Decimal arithmetic the developer *expects* (E-03) but the code never performs:
the principal portion of transfer 1 = `total_payment - interest =
Decimal("1888.36") - (Decimal("300000.00") * Decimal("0.005")) =
Decimal("1888.36") - Decimal("1500.00") = Decimal("388.36")`; expected new
principal = `Decimal("300000.00") - Decimal("388.36") = Decimal("299611.64")`.
The card stays at `$300,000.00` through all four settles because **no code path
computes or writes that subtraction.** Escrow ($400) correctly does NOT reduce
principal (E-01) -- but here *nothing* reduces it. Contrast a FIXED loan:
`get_loan_projection.current_balance` (`:980-984`) would walk to
`$299,611.64`-class on the refinance / debt-strategy / net-worth surfaces, yet
the bold card (`dashboard.html:104`, STORED) would still read `$300,000.00`
(F-014 worked example, `03_consistency.md:1222-1236`).

### Best-evidence root cause

**F-014: no code path recomputes or writes `budget.loan_params.
current_principal` when a transfer to a loan settles** -- code-proven this
session (zero attribute writes; settle modules do not import the model). For an
ARM the stored column is read verbatim everywhere
(`amortization_engine.py:978`, `debt_strategy.py:172-173`,
`savings_dashboard_service.py:840`) and is therefore frozen between manual
edits. For a fixed loan the engine-walk surfaces move but the prominent card
(`dashboard.html:104`) renders STORED regardless of type (F-016).

**Governing intent: E-18 / A-22** (`00_priors.md:184-197`;
`09_open_questions.md:1264-1278`). E-18 reframes both interpretations
(AUTHORITATIVE-maintain-on-settle vs CACHED-display-engine-real) as rejected:
`current_principal` is **RETIRED**. The real principal is derived on read by
one resolver replaying confirmed payments forward from the latest
user-verified anchor (origination being the implicit first anchor); the "edit
current principal" control becomes an append-only dated **anchor event**, not
a scalar overwrite. Remediation direction (fixed, not deferred): delete the
stored-scalar read at every surface (`amortization_engine.py:913,926,978,980`;
`loan/dashboard.html:104`; `savings_dashboard_service.py:840`;
`debt_strategy.py:172-173`) and route them through the single resolver, whose
ARM and fixed paths are the same replay-from-latest-anchor operation (A-22:
the decisive constraint is `:977-978` returning the column verbatim for ARM,
so only event-derivation can produce a *decreasing* ARM balance). Symptom #3
closes because the principal falls as each transfer's principal portion
settles -- it is event-derived, never a frozen scalar.

### Independent-vs-inherited note

**CONFIRMS and strengthens.** C2 map row #3 (`03_consistency.md:6055`) and
drift register row #3 (`04_source_of_truth.md:2140`) assert zero settle-writer.
The independent greps this session reproduce that and **strengthen** it
exactly as `04_source_of_truth.md:673-676` records: the settle path *cannot*
write the column because none of the 12 settle/status modules even imports
`LoanParams` -- a stronger statement than "no writer exists." No
contradiction; the F-014/F-015/F-016 verdicts stand.

### Verification plan (documentation only -- nothing run, no code modified)

1. **Re-confirm the grep set** (the three greps above) on the live tree:
   zero `.current_principal =` writes; the 6-importer set excludes every
   settle module; the 12 settle/status modules reference neither the model nor
   the column.
2. **Settle-path read** (documentation): re-open
   `transfer_service.update_transfer:497-502` and
   `transaction_service.settle_from_entries:38-168` and confirm the only
   mutations are status / paid_at / actual_amount on the parent and the two
   shadows.
3. **DB observation (read-only).** For account 3 with N settled loan-side
   shadow-income rows, confirm `loan_params.current_principal` equals its
   creation/last-manual-edit value (unchanged by the N settles), while
   `get_payment_history` returns N confirmed `PaymentRecord`s -- the proof the
   data to derive the reduction exists but is never applied to this surface.
4. **Equivalence after remediation (regression target).** Once the resolver is
   in place: after each transfer settles, the resolved principal for account 3
   strictly decreases by that transfer's principal portion
   (`total_payment - interest`, escrow excluded per E-01), and every surface
   (card, savings debt card, refinance, debt-strategy, net worth) shows the
   identical resolved value.

---

## Symptom #4 -- 5/5 ARM monthly payment creeps a few dollars month-over-month inside the fixed window

### Symptom (developer's words)

From `financial_calculation_audit_plan.md:606-608`:

> The monthly payment on a 5/5 ARM is fluctuating by a few dollars over
> consecutive months despite being inside the fixed-rate window. This must not
> happen.

### Reproduction path

- **Page:** `/accounts/3/loan`, the bold "Monthly P&I" card
  (`loan/dashboard.html:129` = `summary.monthly_payment`); also every other
  ARM-P&I surface (recurring-transfer prefill `loan.py:1225-1234` site 14;
  savings PITI `savings_dashboard_service.py:846`).
- **Account/period:** account 3, a 5/5 ARM
  (`is_arm = True`, `arm_first_adjustment_months = 60`), viewed on consecutive
  calendar months, all inside months 1-60; no rate change, no manual edit.

### Hypothesis tree (backward from the card across two months; every node Read this session)

```
[CARD m]   summary.monthly_payment  loan/dashboard.html:129
  = calculate_monthly_payment(current_principal, rate, remaining_m)
                                          amortization_engine.py:950-954
    current_principal = STORED  :913        (frozen -- symptom #3 / F-014)
    rate              = STORED  :914        (constant in window -- no RateHistory)
    remaining_m       = calculate_remaining_months(orig, term, today=month m)
                        :908-910 -> :138-142 = term - months_elapsed(m)
[CARD m+1] identical call, one calendar month later
    current_principal = STORED  :913        (STILL frozen)
    rate              = STORED  :914        (STILL constant)
    remaining_(m+1)   = remaining_m - 1     (months_elapsed grew by 1, :138-142)
=> only n changed: M_(m+1) = pmt(P_frozen, r, n-1) != pmt(P_frozen, r, n) = M_m

[FIXED-WINDOW GUARD] is there any code holding M constant for months 1-60?
  arm_first_adjustment_months / arm_adjustment_interval_months
    loan_params.py:60-61  (stored, nullable)
    form-bound  loan.py:670 (_PARAM_FIELDS)
    schema-validated  validation.py:1450-1451 (create) / :1471-1472 (update)
    consumed by ZERO calculation sites (grep this session: only model x2,
    route x1, schema x2; none in amortization_engine.py)
  => the engine has NO representation of the fixed-rate window.
```

### Per-branch finding linkage

- **`n` shrinks while `P` frozen.** Governed by **F-026**
  (`03_consistency.md:1936-2037`, the standalone symptom-#4 finding; verdict
  DIVERGE, SILENT_DRIFT + PLAN_DRIFT vs E-02). Re-verified at source:
  `calculate_remaining_months:138-142`; site-7 `:950-954`; frozen `P` `:913`
  (F-014, symptom #3).
- **No fixed-window concept.** Governed by **F-026**'s structural-root claim;
  grep this session confirms `arm_first_adjustment_months` /
  `arm_adjustment_interval_months` consumed by zero calculation sites
  (`loan_params.py:60-61`, `loan.py:670`, `validation.py:1450-1451,1471-1472`
  only; nothing in `amortization_engine.py`).
- **Rate axis.** AGREE -- no RateHistory rows in months 1-60, so rate is
  identical across surfaces in-window (Q-23/A-23 sub-1); **not the driver**.
  No `RE-INVESTIGATE`.
- **Downstream amplification.** Same site-7 scalar feeds the recurring-transfer
  auto-amount (`loan.py:1225-1234`) and the savings PITI
  (`savings_dashboard_service.py:846`), so the creep also drifts the
  auto-generated transfer amount. Governed by F-026 risk bullet
  (`03_consistency.md:2014-2021`).

### Worked example (hand-recomputed this session, intermediate Decimal steps)

5/5 ARM: `original_principal = Decimal("400000.00")`; STORED
`current_principal = Decimal("400000.00")` (never settle-updated -- symptom #3);
`interest_rate = Decimal("0.06000")` -> `i = Decimal("0.005")`;
`term_months = 360`; `origination_date = 2026-01-01`;
`arm_first_adjustment_months = 60`; no RateHistory in months 1-60.

**Correct constant payment (E-02 requires this value for all 60 months)** --
amortize `original_principal` over the full term, `n = 360`:
`(1.005)^360`: `ln(1.005)=0.004987542`; `*360=1.795515`; `e^1.795515=6.022575`.
`(1.005)^-360 = 1/6.022575 = 0.166042`; `1 - 0.166042 = 0.833958`.
`M* = (400000.00 * 0.005) / 0.833958 = 2000.00 / 0.833958 = Decimal("2398.20")`.

**What site 7 returns, frozen `P = 400000.00`, shrinking `n`:**

`months_elapsed = 24` -> `n = 360 - 24 = 336`:
`(1.005)^336 = e^(0.004987542*336) = e^1.675814 = 5.343555`;
`(1.005)^-336 = 0.187141`; `1 - 0.187141 = 0.812859`;
`M_24 = 2000.00 / 0.812859 = Decimal("2460.45")`.

`months_elapsed = 25` -> `n = 360 - 25 = 335`:
`(1.005)^335 = 5.343555 / 1.005 = 5.316971`;
`(1.005)^-335 = 0.188076`; `1 - 0.188076 = 0.811924`;
`M_25 = 2000.00 / 0.811924 = Decimal("2463.28")`.

Month 24 -> month 25 the displayed Monthly P&I moves
**$2,460.45 -> $2,463.28 (+$2.83)**, entirely inside the fixed-rate window,
with no rate change and no manual edit. Both differ from -- and exceed -- the
correct constant **$2,398.20**. The payment drifts strictly **upward** every
month because `M = P*i / (1 - (1+i)^-n)` with `P`, `i` fixed and `n`
decreasing: `(1+i)^-n` rises, the denominator shrinks, `M` rises. Any one
per-month value already violates E-02 ("Fluctuation by even a few cents is a
finding," `00_priors.md:166-170` via F-026). The few-dollar delta is exactly
the developer's reported shape.

### Best-evidence root cause

**F-026: the engine has no representation of the fixed-rate window
(`arm_first_adjustment_months` consumed by zero calculation sites,
grep-proven) and site 7 (`amortization_engine.py:950-954`) re-amortizes the
FROZEN stored `current_principal` (`:913`, never settle-updated -- symptom #3
/ F-014) over a strictly decreasing `calculate_remaining_months`
(`:908-910` -> `:138-142`).** The amortization identity (re-amortizing the
*true* remaining balance `B_k` over the *remaining scheduled* term reproduces
the original level payment) is broken here precisely because the fed principal
is the frozen stored scalar, not `B_k`.

**Governing intent: E-18 / A-17** (`00_priors.md:184-197`;
`09_open_questions.md:884-895`). A-17 unifies the two earlier interpretations:
the single resolver (a) replays confirmed payments forward from the latest
anchor so the principal it amortizes is the true reduced balance, **and** (b)
honors `arm_first_adjustment_months` / `arm_adjustment_interval_months` so the
payment is constant for the whole fixed window *by construction*, not by
maintenance. Remediation direction (fixed, not deferred): the resolver must
consume the fixed-window columns (today inert at `loan_params.py:60-61`) to
hold the in-window payment at the value amortized from the latest-anchor
balance over the term-from-anchor, instead of `:950-954` re-amortizing a
frozen scalar over a calendar-shrinking `n`. Symptom #4 closes the moment the
principal is event-derived (it decreases as payments clear, never frozen --
the symptom-#3 fix) *and* the window is represented.

### Independent-vs-inherited note

**CONFIRMS.** C2 map row #4 (`03_consistency.md:6056`) and drift register
row #4 (`04_source_of_truth.md:2141`) attribute #4 to F-026: frozen stored
principal re-amortized over shrinking `remaining`, fixed-window columns inert.
The independent grep (zero calculation consumers of the two ARM columns) and
the hand arithmetic ($2,460.45 -> $2,463.28, both != $2,398.20) reproduce the
prior finding exactly. Minor arithmetic note: this session's month-24 value
rounds to $2,460.45 (matching `03_consistency.md:1983`); the P4 table
(`04_source_of_truth.md:1129`) shows $2,460.50 for the same input -- a
sub-dime difference from the precision of the hand-evaluated `(1.005)^336`,
immaterial to the +$2.83/month drift conclusion and not a verdict
discrepancy.

### Verification plan (documentation only -- nothing run, no code modified)

1. **Re-confirm the inert-column grep**: `arm_first_adjustment_months` /
   `arm_adjustment_interval_months` appear only in `loan_params.py:60-61`,
   `loan.py:670`, `validation.py:1450-1451,1471-1472`; zero hits in
   `amortization_engine.py`.
2. **Two-month hand check** with account 3's real `(P, r, term,
   origination)`: confirm `calculate_monthly_payment(current_principal, rate,
   T - e)` and `... T - (e+1))` differ by a few dollars and that both exceed
   the origination-amortized constant -- the E-02 violation.
3. **Identity check (falsification).** Compute the *true* amortized balance
   `B_e` after `e` scheduled payments and confirm
   `calculate_monthly_payment(B_e, rate, T - e)` reproduces the origination
   constant to the cent -- proving the driver is the frozen `P`, not the
   formula. If it does not, the formula itself (`:194-197`) must be
   re-investigated.
4. **Equivalence after remediation (regression target).** With the E-18
   resolver honoring the fixed window: for account 3, the resolved
   monthly_payment is bit-identical for every evaluation date in
   `[origination, origination + arm_first_adjustment_months)` and equals the
   value amortized from the latest-anchor balance over the term-from-anchor.

---

## Do symptoms #2, #3, #4 collapse onto one un-maintained column?

**Yes -- with one precisely-bounded qualification.** Independently re-proven
from source this session:

- **Shared root column.** All three are downstream of
  `budget.loan_params.current_principal` having **no settle-driven update
  path** (grep this session: zero `.current_principal =` writes; the 12
  settle/status modules do not import `LoanParams`). Symptom #3 *is* that
  fact, observed directly on the card (`dashboard.html:104`). Symptom #4 is
  that same frozen scalar (`amortization_engine.py:913`) re-amortized over a
  calendar-shrinking `calculate_remaining_months` (`:908-910`->`:138-142`) at
  the ARM scalar site (`:950-954`). Symptom #2's cross-session face is symptom
  #4 read off the Monthly-P&I card; its after-edit drop is the *same* column's
  sole writer (`loan.py:672-674`) moving it.
- **The qualification (symptom #2 only).** Symptom #2 additionally carries the
  F-013 *same-day, cross-surface* `n`-source axis: the scalar site
  (`n = T - months_elapsed`, `:908-910`) and the schedule anchor row
  (`n = max_months - month_num + 1`, `:490`, `max_months = term_months`
  `:455`) feed `calculate_monthly_payment` different `n` for the same loan on
  the same day. This is **not a second column** -- it is the same E-18 disease
  (no single resolver; every surface assembles its own `(P, r, n)`), and the
  off-by-one only *exists* because `P` is a frozen scalar rather than an
  event-derived balance owned by one resolver. So #2/#3/#4 collapse onto the
  one un-maintained `current_principal` column, and #2's extra wobble collapses
  onto the same E-18 root (absence of one resolver), not onto a distinct
  cause.
- **One governing expectation, one remediation.** E-18 (`00_priors.md:184-197`)
  with A-17 / A-22 / A-23 (answered as a single stored-mirror-retirement
  policy, `09_open_questions.md:884-895,1264-1278,1390-1409`) fixes all three
  with one change: retire the stored `current_principal` (and
  `interest_rate`) scalar, derive (balance, monthly_payment, schedule) on read
  from the event stream via one resolver that replays confirmed payments
  forward from the latest user-verified anchor and honors the ARM fixed-window
  columns. #3 closes (principal falls as transfers settle), #4 closes (in-window
  payment constant by construction), #2 closes (one resolver -> one `(P,r,n)`
  -> one value on every surface, every session). The C2 map's "collapse onto
  one un-maintained column" assertion is **CONFIRMED**, narrowed only by
  naming F-013's contribution to #2 explicitly as the same-root multi-surface
  `n` axis rather than an independent defect.

---

## Symptom #5 -- account balances on `/accounts` do not match the balances shown anywhere else

### Symptom (developer's words)

From `financial_calculation_audit_plan.md:609-611`:

> Account balances on `/accounts` do not match the balances shown
> anywhere else in the app.

### Reproduction path

- **Pages (the five balance producers, F-001's enumerated set):**
  - **B = `/accounts`** -- the page the developer names. Checking:
    `accounts.checking_detail` "Current Balance" tile
    (`accounts.py:1379-1468`, `current_bal` at `:1432`). Loan: the
    `/accounts/<id>/loan` dashboard "Current Principal" card
    (`loan/dashboard.html:104`).
  - **A = grid** balance row, current period (`grid.py:167-248`
    index, `:396-467` HTMX `balance_row`).
  - **C = `/savings`** account card (`savings_dashboard_service.
    _compute_account_projections`, `:294-400`; checking `current_bal`
    `:352`, loan `current_bal = proj.current_balance` `:373`) plus the
    same page's debt-summary widget (`_compute_debt_summary` `:802-876`,
    `total_debt` from stored `current_principal` `:840,855`).
  - **D = dashboard** balance card (`dashboard_service._compute_balances`
    `:673-705`; `_get_balance_info` `:334-372`, `current_balance` `:349`).
  - **E = net worth** per-account input (`year_end_summary_service.
    _get_account_balance_map` `:2036-2128`).
- **Account/period/scenario:** ONE tuple is carried below -- user `U`,
  the user's default **Checking** account, the **current** pay period
  `P0` (which is also that account's anchor period so `_sum_remaining`
  applies, `balance_calculator.py:72-75`), the **baseline** scenario
  (every producer uses `get_baseline_scenario`: `grid.py:177`,
  `accounts.py:1400`, `savings_dashboard_service.py:85`,
  `dashboard_service` via caller, `year_end_summary_service.py:2089`).
- **User input that exhibits it:** the current period holds at least one
  *Projected* envelope expense whose `TransactionEntry` rows are partly
  cleared (or carry credit entries) -- exactly the symptom-#1 precondition,
  here observed across all five producers rather than two. A second,
  wider facet appears when `current_anchor_period_id IS NULL` (the
  default new-user state, E-19 proof substrate `00_priors.md:210`); a
  third facet is any loan account, where the displayed principal has
  three different bases.

### Hypothesis tree (backward from each producer's displayed number; every node Read this session)

Checking facet -- all five producers call the SAME pure engine
`balance_calculator.calculate_balances` (`balance_calculator.py:35-109`,
Read in full this session). The tree walks each producer back to the
first input that differs.

```
[B = /accounts] accounts.checking_detail  accounts.py:1432
  current_bal = balances.get(current_period.id) if current_period
                else anchor_balance              accounts.py:1432
  B1. balances = calculate_balances(...)          accounts.py:1423-1430
        anchor_balance   = account.current_anchor_balance or 0.00   :1418
        anchor_period_id = account.current_anchor_period_id
                           or (current_period.id if current_period
                               else None)                           :1419-1421
        transactions     = acct_transactions, query :1407-1416
                           *** NO selectinload(entries) ***  :1407-1416
  B2. engine _sum_remaining -> _entry_aware_amount(txn)
        'entries' in txn.__dict__ ? -> FALSE -> return effective_amount
                                       balance_calculator.py:353-354
  B3. effective_amount -> estimated_amount (Projected, actual None)
                                       transaction.py:238-245

[A = grid] grid balance row  grid.py:243-248 / :446-451
  A1. anchor_balance   = account.current_anchor_balance if account
                         else 0.00                       grid.py:238 / :443
      anchor_period_id = account.current_anchor_period_id if account
                         else current_period.id           grid.py:239-241 / :444
      transactions     = all_transactions, query :226-234 / :436-441
                         *** selectinload(entries) PRESENT ***
                         grid.py:229  AND  grid.py:438
  A2. engine _sum_remaining -> _entry_aware_amount(txn)
        'entries' in txn.__dict__ ? -> TRUE -> entry formula
        max(estimated - cleared_debit - sum_credit, uncleared_debit)
                                       balance_calculator.py:383-386

[C = /savings] _compute_account_projections  savings_dashboard_service.py:352
  C1. current_bal = balances.get(current_period.id) ...   :352
      anchor_balance   = acct.current_anchor_balance or 0.00      :325
      anchor_period_id = acct.current_anchor_period_id
                         or (current_period.id if current_period
                             else None)                            :326-328
      transactions     = acct_transactions filtered :320-323 from
                         preload :92-100
                         *** NO selectinload(entries) ***  :92-100
  C2. engine path identical to B2 -> effective_amount  :353-354
  [C-loan] loan branch: current_bal = proj.current_balance  :373
           proj = get_loan_projection(...)               :362-366
  [C-debt] _compute_debt_summary total_debt += Decimal(str(
           lp.current_principal))   :840,855  (STORED column)

[D = dashboard] _compute_balances + _get_balance_info
  D1. _compute_balances :673-705
        if not periods or account.current_anchor_period_id is None:
            return None                 dashboard_service.py:683-684
        transactions: query :687-696
                      *** selectinload(entries) PRESENT ***  :689
        calculate_balances(account.current_anchor_balance,
                            account.current_anchor_period_id,
                            periods, txns)                   :699-703
  D2. engine _sum_remaining -> _entry_aware_amount entry formula
        (entries loaded at :689) -> :383-386
  D3. _get_balance_info :348-353
        current_balance = balance_results.get(current_period.id,
                            account.current_anchor_balance or _ZERO)  :349-350

[E = net worth] _get_account_balance_map  year_end_summary_service.py:2127
  E1. if account.current_anchor_period_id is None: return None  :2065-2066
  E2. checking (not amortization/interest/investment) ->
        calculate_balances(**base_args)                  :2127
        anchor_balance   = account.current_anchor_balance or ZERO  :2096
        anchor_period_id = account.current_anchor_period_id        :2099
        transactions     = query :2085-2094
                           *** NO selectinload(entries) ***  :2085-2094
  E3. engine path identical to B2/C2 -> effective_amount  :353-354
  [E-loan] amortization branch: _schedule_to_period_balance_map(
           debt_schedules[account.id], periods,
           params.original_principal)   :2079-2081  (SCHEDULE)
```

The five backward walks are identical at every node -- same engine,
same baseline scenario, same `all_periods`, same `is_deleted` query
gate, same Projected-only status gate, same income path
(`txn.effective_amount` both sides, no entries dependence) -- until the
single divergent input: whether the *consuming query* issued
`selectinload(Transaction.entries)`. **A and D loaded entries**
(`grid.py:229`/`:438`, `dashboard_service.py:689`) -> entry formula
(`balance_calculator.py:383-386`). **B, C, E did not load entries**
(`accounts.py:1407-1416`, `savings_dashboard_service.py:92-100`,
`year_end_summary_service.py:2085-2094`) -> `effective_amount`
short-circuit (`:353-354`). One account, one period, two clusters of
displayed value. A second axis (`current_anchor_period_id IS NULL`) and
a third (loan-principal base) widen the spread further (below).

### Per-branch finding linkage

- **B/C/E vs A/D -- the entries-load expense divergence (the primary
  checking driver).** Governed by **F-001** (`03_consistency.md:109-211`,
  the symptom-#5 dedicated finding; verdict DIVERGE, the
  "Effective-amount logic: DIVERGES" dimension `:149-151` and the
  "Expense formula" divergence `:173-176`, SILENT_DRIFT). Same mechanism
  as **F-009/F-002/F-003** (symptom #1). Resolved intent **E-25**
  (`00_priors.md:276-284`, one entry-aware producer) and the E-04
  unlabeled-divergence prior. Re-verified at source this session:
  `selectinload` PRESENT `grid.py:229`, `grid.py:438`,
  `dashboard_service.py:689`; ABSENT `accounts.py:1407-1416`,
  `savings_dashboard_service.py:92-100`,
  `year_end_summary_service.py:2085-2094`; engine short-circuit
  `balance_calculator.py:353-354` vs entry formula `:383-386`.

- **Anchor-None handling -- four behaviors for one missing-anchor
  input.** Governed by the **F-001/F-003 anchor-None SCOPE axis**
  (`03_consistency.md:153-160,177-178`), Q-16/Q-20 -> **E-19**
  (`00_priors.md:198-213`). Re-verified at source this session: grid
  passes the NULL column through (`grid.py:239-241`,
  `account.current_anchor_period_id if account else current_period.id`
  -- `account` truthy so the NULL value, not `current_period.id`, is
  passed; `balance_calculator.py:82-84` then yields no anchor match ->
  empty dict -> **blank row**); `/accounts` and `/savings` fall back to
  `current_period.id` (`accounts.py:1419-1421`,
  `savings_dashboard_service.py:326-328`) -> **a populated projection**;
  dashboard and net worth `return None` (`dashboard_service.py:683-684`,
  `year_end_summary_service.py:2065-2066`) -> **account omitted**. Four
  behaviors (blank / projection / omit), and the projection itself is
  seeded with `current_anchor_balance or 0.00` so it is the *stored
  balance* at the wrong period, not literally `$0.00`
  (`04_source_of_truth.md:115-119`, re-read this session).

- **Loan-principal base -- three sources for one loan (F sub-comparison).**
  Governed by **F-001 path F** (`03_consistency.md:120-123,179-181`,
  SOURCE_DRIFT) and **F-008** (`03_consistency.md:580-647`, the
  internal stored-vs-engine inconsistency that holds independent of
  Q-15, `:621-627`). Re-verified at source this session:
  `/accounts` loan card renders STORED `params.current_principal`
  (`loan/dashboard.html:104`); `/savings` account card uses
  `proj.current_balance` (`savings_dashboard_service.py:373`, engine --
  ARM returns stored verbatim per `amortization_engine.py:977-978`,
  fixed walks the schedule); the same `/savings` page's debt card sums
  STORED `current_principal` (`savings_dashboard_service.py:840,855`);
  net-worth liability uses the amortization SCHEDULE
  (`year_end_summary_service.py:2079-2081`). Resolved intent **E-18**
  (`00_priors.md:184-197`) -- the un-maintained-mirror root proven in
  symptoms #2/#3/#4 (recovery state above; not re-derived here per the
  P5-c reading bound).

- **Dual per-account dispatcher.** `_compute_account_projections`
  (`savings_dashboard_service.py:294`, drives C + dashboard cards) and
  `_get_account_balance_map` (`year_end_summary_service.py:2036`,
  drives E + net worth) implement per-account balance dispatch twice
  with no canonical owner. Governed by **F-001** "Per-account dispatch
  is implemented twice" (`03_consistency.md:186-189`, PLAN_DRIFT, was
  Q-15). The single-resolver direction of **E-18/E-19** subsumes it
  (one resolver, one dispatcher) -- recorded as governed, not left
  blocked (phase5_plan section 0: Q-08..Q-26 answered).

- **Grid account-scope (low-incidence).** `grid.py:224-225` appends the
  `account_id` filter only `if account`; B/C/D/E always scope by
  `account_id`. If `resolve_grid_account` returns None the grid sums
  every account's transactions while the others do not (`grid.py:224`
  Read this session: `if account: txn_filters.append(Transaction.
  account_id == account.id)`; anchor `if account else 0.00` `:238`).
  Governed by **F-001** "Grid account scoping" (`03_consistency.md:182-185`,
  SCOPE, low-incidence); the E-19 resolver-owns-the-account direction
  removes it. No `RE-INVESTIGATE`.

- **Income / status filter / scenario / is_deleted / period scope /
  quantization.** All AGREE across the five producers, re-confirmed at
  source this session (income `txn.effective_amount` no entries
  dependence, `balance_calculator.py:415`/`:447`; Projected-only gate
  `:411-412`/`:443-444`; baseline scenario at all five call sites cited
  above; `is_deleted.is_(False)` in all five queries `grid.py:222`,
  `accounts.py:1413`, `savings_dashboard_service.py:97`,
  `dashboard_service.py:694`, `year_end_summary_service.py:2091`; full
  `all_periods` anchor-forward; no quantization in the projected-sum
  path). **No NO-FINDING -> RE-INVESTIGATE branch:** every divergent
  node maps to F-001 or F-008; every AGREE node was independently
  confirmed at source. Phase 3's completeness on symptom #5 is confirmed,
  not patched.

### Worked example (one tuple, all five producers; hand-recomputed this session, intermediate Decimal steps)

Carried tuple: user `U`; default **Checking** account; **current** pay
period `P0` == that account's anchor period; **baseline** scenario.
Inputs (all `Decimal`, constructed from strings per coding-standards;
continuous with symptom #1's reproduction so the gap is the same
$45.71 mechanism, here shown at five producers):

- `account.current_anchor_balance = Decimal("614.29")`,
  `account.current_anchor_period_id = P0.id` (SET -- not None, so this
  facet isolates the entries-load axis from the anchor-None axis).
- One Projected expense "Groceries": `estimated_amount =
  Decimal("500.00")`, `actual_amount = None`, `status = Projected`.
- Its entries: three cleared debits `Decimal("20.00")`,
  `Decimal("15.71")`, `Decimal("10.00")` (`is_credit=False`,
  `is_cleared=True`); no credit, no uncleared.

  `cleared_debit   = Decimal("20.00") + Decimal("15.71")
                    + Decimal("10.00") = Decimal("45.71")`
  `uncleared_debit = Decimal("0")` ; `sum_credit = Decimal("0")`
- No income in `P0` (income is identical across all producers and
  cancels).

Engine, `P0 == anchor period` -> `_sum_remaining`, `running_balance =
anchor_balance + income - expenses` (`balance_calculator.py:75`).

**Producers A and D (entries loaded: `grid.py:229`/`:438`,
`dashboard_service.py:689`).** `_entry_aware_amount` reaches the entry
formula (`balance_calculator.py:383-386`):

```
expense = max(estimated_amount - cleared_debit - sum_credit,
              uncleared_debit)
        = max(Decimal("500.00") - Decimal("45.71") - Decimal("0"),
              Decimal("0"))
        = max(Decimal("454.29"), Decimal("0")) = Decimal("454.29")
running  = Decimal("614.29") + Decimal("0.00") - Decimal("454.29")
         = Decimal("160.00")
```

- **A (grid):** `balances[P0.id] = Decimal("160.00")` -> grid shows
  **$160.00**.
- **D (dashboard):** `_get_balance_info` `current_balance =
  balance_results.get(P0.id, ...)` (`dashboard_service.py:349`) =
  **$160.00**.

**Producers B, C, E (entries NOT loaded: `accounts.py:1407-1416`,
`savings_dashboard_service.py:92-100`,
`year_end_summary_service.py:2085-2094`).** `_entry_aware_amount`
short-circuits (`'entries' not in txn.__dict__`,
`balance_calculator.py:353-354`) -> `txn.effective_amount`;
`effective_amount` (`transaction.py:238-245`): not deleted, Projected
not `excludes_from_balance`, `actual_amount is None` ->
`estimated_amount = Decimal("500.00")`:

```
expense = Decimal("500.00")
running = Decimal("614.29") + Decimal("0.00") - Decimal("500.00")
        = Decimal("114.29")
```

- **B (`/accounts` checking_detail):** `current_bal =
  balances.get(P0.id)` (`accounts.py:1432`) = **$114.29**.
- **C (`/savings`):** `current_bal = balances.get(P0.id)`
  (`savings_dashboard_service.py:352`) = **$114.29**.
- **E (net worth):** per-account map `[P0.id]`
  (`year_end_summary_service.py:2127`) = **$114.29**.

**The per-page spread for ONE (U, P0, baseline, Checking) tuple, no
error raised anywhere:**

| Producer | Page | path:line | Value |
|---|---|---|---|
| B | **`/accounts`** checking detail | `accounts.py:1432` (no entries `:1407-1416`) | **$114.29** |
| A | grid balance row | `grid.py:243-248` (entries `:229`) | **$160.00** |
| C | `/savings` account card | `savings_dashboard_service.py:352` (no entries `:92-100`) | **$114.29** |
| D | dashboard balance card | `dashboard_service.py:349` (entries `:689`) | **$160.00** |
| E | net-worth input | `year_end_summary_service.py:2127` (no entries `:2085-2094`) | **$114.29** |

`gap = Decimal("160.00") - Decimal("114.29") = Decimal("45.71") =
cleared_debit` -- the grocery dollars already cleared in the bank
(hence already in the real anchor `$614.29`); the entries-unloaded
producers subtract them a second time. The developer's exact words
("`/accounts` does not match the balances shown anywhere else") are
**sharpened by the trace**: `/accounts` ($114.29) in fact *matches*
`/savings` and the net-worth input (the B=C=E cluster) but mismatches
the grid and dashboard ($160.00, the A=D cluster) -- the two surfaces
used daily -- by exactly $45.71. It is not "nowhere"; it is a two-
cluster split and `/accounts` sits in the cluster that excludes the
primary screens.

**Second facet (same tuple, `current_anchor_period_id` now NULL -- the
default new-user state, `00_priors.md:210`).** One stored balance, five
producers, four behaviors (hand-traced this session):

| Producer | Behavior with NULL anchor period | path:line |
|---|---|---|
| A grid | passes NULL through -> empty dict -> **blank row** | `grid.py:239-241`; `balance_calculator.py:82-84` |
| B `/accounts` | `or current_period.id` -> **projection seeded with stored balance at current period** | `accounts.py:1419-1421` |
| C `/savings` | same fallback -> **same projection** | `savings_dashboard_service.py:326-328` |
| D dashboard | `return None` -> **account omitted** | `dashboard_service.py:683-684` |
| E net worth | `return None` -> **account omitted** | `year_end_summary_service.py:2065-2066` |

`/accounts` shows a populated projection while the grid is blank and
the dashboard/net-worth omit the account entirely -- the developer's
"matches nowhere" is *literally* true in this anchor-None facet.

**Third facet (same user, loan account = the mortgage).** Cross-
referenced from symptoms #2/#3 (recovery state, not re-derived per the
P5-c reading bound): for a **fixed-rate** loan with confirmed payments
(`04_source_of_truth.md:618-644`), `/accounts` loan card renders STORED
`$200,000.00` (`loan/dashboard.html:104`), `/savings` account card the
engine `$199,399.70` (`savings_dashboard_service.py:373`), the same
`/savings` debt card STORED `$200,000.00`
(`savings_dashboard_service.py:840,855`), net-worth liability the
schedule `$199,399.70` (`year_end_summary_service.py:2079-2081`),
debt-strategy the RAW replay `$198,495.20` -- three distinct bases for
one loan-on-date. For the developer's ARM mortgage every base reads the
stored column verbatim, so the loan facet is degenerate ($300,000
everywhere, frozen) -- symptom #3.

### Best-evidence root cause

Symptom #5 is **not a new root cause; it is the union of three already-
rooted divergences observed across the five F-001 producers
simultaneously**, which is exactly why "`/accounts` matches nowhere"
is the lived experience:

1. **The entries-load checking divergence (F-001 / F-009).** The same
   `selectinload(Transaction.entries)` asymmetry that drives symptom #1,
   here generalized: A/D load entries (entry formula,
   `balance_calculator.py:383-386`), B/C/E do not
   (`effective_amount` short-circuit `:353-354`). Governing intent
   **E-25** (`00_priors.md:276-284`) -- one entry-aware producer.
2. **The anchor-None SCOPE divergence (F-001/F-003).** Four behaviors
   for `current_anchor_period_id IS NULL`. Governing intent **E-19**
   (`00_priors.md:198-213`) -- the NULL-period state is *eliminated*
   (date-anchored resolver, guaranteed t0 anchor+history), not
   per-consumer NULL-branched.
3. **The loan-principal base divergence (F-001 path F / F-008).** Three
   sources (stored / engine / schedule) for one loan. Governing intent
   **E-18** (`00_priors.md:184-197`) -- `current_principal` retired,
   one resolver derives balance/payment/schedule from the event stream.

Stated as a cited hypothesis: there is **no canonical balance producer**.
Five consumers each assemble their own `(anchor, anchor_period,
entries-loaded?, dispatcher)` and read three different loan bases, and
the engine `_entry_aware_amount` was written to *degrade silently*
(`balance_calculator.py:353-354`) when entries are absent -- so the
displayed balance is a function of which page's query happened to
eager-load `entries`, whether that page NULL-branches the anchor, and
which of two per-account dispatchers it routes through. **Remediation
direction (fixed by E-18/E-19, stated not deferred):** one date-anchored
resolver owns "balance as of period for account" and "loan principal as
of date"; it guarantees the entry-aware reduction (entries loaded or the
reduction computed inside it -- the E-25 single producer), eliminates
the NULL-period state (E-19, so no blank/omit/fallback fork), and derives
loan principal from the event stream (E-18, so stored/engine/schedule
collapse to one). Every page -- `/accounts`, grid, `/savings`,
dashboard, net worth -- then reads the IDENTICAL number from that one
resolver (the E-04 invariant). The dual dispatcher
(`savings_dashboard_service.py:294` vs
`year_end_summary_service.py:2036`) collapses to that single resolver;
the grid account-scope branch (`grid.py:224-225`) is removed because the
resolver owns account identity.

### Independent-vs-inherited note

**CONFIRMS C2 row #5 and the drift register row #5, with one wording
sharpening and the dual-dispatch axis re-classified as governed.**

- **Confirms.** C2 row #5 (`03_consistency.md:6057`) asserts symptom #5
  = **F-001 + F-008**: grid/dashboard `$962.34` (entry formula) vs
  `/savings`//`/accounts`/net-worth `$500.00` (effective_amount) for one
  Projected envelope expense, plus anchor-None 4-way SCOPE + loan-base
  3-way SOURCE. The independent backward trace from all five producers
  this session lands on exactly that: A=D entry formula, B=C=E
  effective_amount; the four anchor-None behaviors; the three loan
  bases. The hand-recompute reproduces the F-001 worked example's
  structure (`03_consistency.md:190-201`, anchor `$1,000`, cleared
  `$462.34` -> `$962.34` vs `$500.00`) with the symptom-#1-continuous
  numbers (anchor `$614.29`, cleared `$45.71` -> `$160.00` vs
  `$114.29`); both gaps equal `cleared_debit`. Drift register row #5
  (`04_source_of_truth.md:2142`) "unlabeled per-page base divergence
  ... E-04 violated" is confirmed.
- **Sharpening (not a contradiction).** The developer's and C2's phrase
  "matches **nowhere** else" is imprecise for the anchor-SET checking
  facet: the trace proves a **two-cluster split** -- B(`/accounts`)=C
  (`/savings`)=E(net-worth) at one value, A(grid)=D(dashboard) at
  another. `/accounts` *does* match two surfaces; it mismatches the two
  most-used ones. The literal "nowhere" holds only in the anchor-None
  facet (blank / projection / omit are genuinely all different). F-001's
  DIVERGE verdict and proven divergence facts are unchanged; only the
  cardinality of the mismatch is made precise.
- **Re-classified as governed (not blocked).** Drift register row #5
  lists Q-16, **Q-20**, Q-22 as co-blocking, and F-001/F-008 carried
  Q-15 (canonical dispatcher) as UNKNOWN. Per phase5_plan section 0
  every Q-08..Q-26 is answered: Q-16/Q-20 -> E-19, Q-22 -> E-18, and
  the Q-15 dual-dispatcher / canonical-base axis is subsumed by the
  single-resolver direction of E-18/E-19. No symptom-#5 axis is left
  blocked; the prior phases' UNKNOWN/Q-15 deferral is closed by the
  locked E-NN, which is a resolution, not a contradiction.

### Verification plan (documentation only -- nothing run, no code modified)

1. **Confirm the five-way source asymmetry persists (code read).**
   Re-open and confirm `selectinload(Transaction.entries)` PRESENT at
   `grid.py:229`, `grid.py:438`, `dashboard_service.py:689`; ABSENT at
   `accounts.py:1407-1416`, `savings_dashboard_service.py:92-100`,
   `year_end_summary_service.py:2085-2094`. Confirm
   `_entry_aware_amount` short-circuit `balance_calculator.py:353-354`
   and entry formula `:383-386`.
2. **Reconstruct the developer's actual spread (read-only DB).** For
   the developer's checking account, current period, baseline: pull all
   Projected expense transactions with their `TransactionEntry`
   (`amount`, `is_credit`, `is_cleared`) and `estimated_amount`; compute
   per-transaction `estimated_amount - max(estimated_amount -
   cleared_debit - sum_credit, uncleared_debit)`, sum over the period;
   assert it equals (grid balance) - (`/accounts` balance). If not, the
   entries-load axis is not the sole checking driver and the AGREE nodes
   (anchor, scenario, period scope) must be re-investigated -- the
   falsification test.
3. **Anchor-None branch check.** For an account with
   `current_anchor_period_id IS NULL` and `current_anchor_balance` set,
   hand-trace all five producers; confirm grid blank
   (`grid.py:239-241` -> empty dict), `/accounts`+`/savings` populated
   from the stored balance (`accounts.py:1419-1421`,
   `savings_dashboard_service.py:326-328`), dashboard+net-worth omitted
   (`dashboard_service.py:683-684`,
   `year_end_summary_service.py:2065-2066`).
4. **Loan three-base check.** For a fixed-rate loan with >=1 confirmed
   payment, confirm `/accounts` loan card = STORED
   (`loan/dashboard.html:104`), `/savings` account card =
   `proj.current_balance` (`savings_dashboard_service.py:373`),
   `/savings` debt card = STORED (`:840,855`), net-worth = schedule
   (`year_end_summary_service.py:2079-2081`) -- three values, one loan
   (cross-ref symptom #3 worked example).
5. **Equivalence after remediation (regression target, documented).**
   Once the single E-18/E-19 resolver is in place: for any
   account/period/scenario,
   `accounts.current_bal == grid.balances[p] == savings.current_bal ==
   dashboard.current_balance == networth.balance_map[p]`, and for any
   loan-on-date the principal is one event-derived value on all four
   loan surfaces -- the E-04 invariant, with no NULL-period fork and no
   entries-load dependence.

---

## P5-d -- Verification and consolidation gate (trust-but-verify capstone)

Session P5-d. No new symptom analysis. Every claim below was re-resolved to
live source this session (`grep`/`sed`/`Read`); Phase 3/4 and prior-session
citations were re-opened, not quoted from memory.

### 1. Spot-check -- 22 cited claims re-resolved at random across Symptom #1..#5

Threshold 100%. Each row is a claim drawn from a symptom tree / linkage /
worked example, the exact source re-read this session, and the verdict.

| # | Symptom | Cited claim (from 05_symptoms.md) | Re-resolved source (this session) | Verdict |
|---|---|---|---|---|
| 1 | #1 | `calculate_balances` engine at `balance_calculator.py:35-109` | `balance_calculator.py:35` `def calculate_balances(anchor_balance, anchor_period_id, periods, transactions):` | PASS |
| 2 | #1 | anchor-period `running_balance = anchor_balance + income - expenses` `:72-75` | `balance_calculator.py:72-75` `if period.id == anchor_period_id: income, expenses = _sum_remaining(...); running_balance = anchor_balance + income - expenses` | PASS |
| 3 | #1 | `_entry_aware_amount` short-circuit `balance_calculator.py:353-354` | `balance_calculator.py:353-354` `if 'entries' not in txn.__dict__: return txn.effective_amount` | PASS |
| 4 | #1 | entry formula `max(estimated - cleared_debit - sum_credit, uncleared_debit)` `:383-386` | `balance_calculator.py:383-386` `return max(txn.estimated_amount - cleared_debit - sum_credit, uncleared_debit,)` | PASS |
| 5 | #1 | grid eager-loads entries at `grid.py:229` and `grid.py:438` | `grid.py:226-234` and `:436-441` both `.options(selectinload(Transaction.entries) ...)` | PASS |
| 6 | #1 | `effective_amount` returns `estimated_amount` when `actual_amount is None` `transaction.py:238-245` | `transaction.py:245` `return self.actual_amount if self.actual_amount is not None else self.estimated_amount` (preceded by is_deleted/excludes_from_balance guards `:238-241`) | PASS |
| 7 | #1 (W-277) | `calendar_service.py:471-480` query has NO `selectinload`, then `calculate_balances` `:482-487` | `calendar_service.py:471-479` `db.session.query(Transaction).filter(...).all()` (no `.options`); `:481-487` `balance_calculator.calculate_balances(account.current_anchor_balance, account.current_anchor_period_id, all_periods, all_txns,)` | PASS |
| 8 | #2 | site-7 ARM scalar `amortization_engine.py:950-954` `calculate_monthly_payment(current_principal, rate, remaining)` | `:950-954` `if is_arm and remaining > 0: monthly_payment = calculate_monthly_payment(current_principal, rate, remaining,)` | PASS |
| 9 | #2 | `calculate_remaining_months` `:138-142` `max(0, term_months - months_elapsed)`, `as_of` defaults today | `:128-142` `if as_of is None: as_of = date.today()`; `months_elapsed = (as_of.year-orig.year)*12 + (as_of.month-orig.month)`; `return max(0, term_months - months_elapsed)` | PASS |
| 10 | #2 | engine annuity `:194-197` `i=r/12; f=(1+i)**n; M=(P*i*f/(f-1)).quantize(HALF_UP)` | `:194-197` `monthly_rate = annual_rate/12; factor = (1+monthly_rate)**remaining_months; payment = principal*(monthly_rate*factor)/(factor-1); return payment.quantize(TWO_PLACES, ROUND_HALF_UP)` | PASS |
| 11 | #2 | schedule narrowing: `using_contractual` FALSE for ARM `:430-434`; `max_months = remaining_months` `:455`; `remaining_months = params.term_months` | `:430-434` `using_contractual = (original_principal is not None and term_months is not None and not has_rate_changes)`; `:454-455` `else: max_months = remaining_months`; call site `:932` passes `params.term_months` as the 3rd positional arg, which `def generate_schedule` `:326-329` binds to `remaining_months`; `original = None if is_arm` `:920` | PASS |
| 12 | #2 | schedule anchor reset `:486-493` `months_left = max_months - month_num + 1`, recompute payment | `:486-493` `if (... pay_date > anchor_date): balance = anchor_balance; ...; months_left = max_months - month_num + 1; monthly_payment = calculate_monthly_payment(balance, current_annual_rate, months_left,)` | PASS |
| 13 | #2/#3 | sole post-creation writer `loan.py:668-674` `setattr(params, field, value)` with `"current_principal" in _PARAM_FIELDS` | `loan.py:668-674` `_PARAM_FIELDS = {"current_principal", ...}; for field, value in data.items(): if field in _PARAM_FIELDS: setattr(params, field, value)` | PASS |
| 14 | #3 | zero `.current_principal =` writes anywhere in `app/`/`scripts/` | `grep -rEn "\.current_principal\s*=[^=]" app/ scripts/ --include='*.py'` -> zero matches (re-run this session) | PASS |
| 15 | #3 | card renders STORED `loan/dashboard.html:104`; `proj` computed `loan.py:429-431` but passed `params=params` `:553-557` (not wired) | `dashboard.html:104` `${{ "{:,.2f}".format(params.current_principal|float) }}`; `loan.py:429-431` `proj = amortization_engine.get_loan_projection(...)`; `:553-557` `render_template("loan/dashboard.html", ..., params=params,)` | PASS |
| 16 | #3 | ARM `cur_balance = current_principal` verbatim `amortization_engine.py:977-978`; fixed walks reversed schedule `:980-984` | `:977-978` `if is_arm: cur_balance = current_principal`; `:980-984` `else: cur_balance = current_principal; for row in reversed(schedule): if row.is_confirmed: cur_balance = row.remaining_balance; break` | PASS |
| 17 | #3 | importer set excludes every settle module ("no settle module imports LoanParams") | `grep -rln "import LoanParams\|loan_params import\|from app.models.loan_params"` -> `debt_strategy.py, loan.py, accounts.py, models/__init__.py, loan_payment_service.py, year_end_summary_service.py, savings_dashboard_service.py`; no `transfer_service`/`transaction_service`/`state_machine`/`recurrence`/`status` module present | PASS (see nuance below) |
| 18 | #4 | `arm_first_adjustment_months`/`arm_adjustment_interval_months` consumed by ZERO calc sites; none in `amortization_engine.py` | `grep -rn` -> only `loan_params.py:60-61`, `loan.py:670`, `validation.py:1450-1451,1471-1472`; zero hits in `amortization_engine.py` | PASS |
| 19 | #4 | `current_principal` is `Numeric(12,2) NOT NULL` `loan_params.py:54` | `loan_params.py:54` `current_principal = db.Column(db.Numeric(12, 2), nullable=False)` | PASS |
| 20 | #5 | B `/accounts` no `selectinload` `:1407-1416`; `current_bal = balances.get(current_period.id) if current_period else anchor_balance` `:1432` | `accounts.py:1407-1416` query `.filter(...).all()` (no `.options`); `:1432` exactly as cited; anchor fallback `:1419-1421` `or (current_period.id if current_period else None)` | PASS |
| 21 | #5 | D dashboard loads entries `dashboard_service.py:689`; anchor-None `return None` `:683-684` | `:683-684` `if not periods or account.current_anchor_period_id is None: return None`; `:689` `.options(selectinload(Transaction.entries))` | PASS |
| 22 | #5 | E net worth no `selectinload` `:2085-2094`; anchor-None `return None` `:2065-2066`; loan branch = schedule `:2079-2081` | `year_end_summary_service.py:2065-2066` `if account.current_anchor_period_id is None: return None`; `:2079-2081` `return _schedule_to_period_balance_map(debt_schedules[account.id], periods, original,)`; `:2085-2094` query no `.options`; `:2127` `calculate_balances(**base_args)` | PASS |

**Spot-check result: 22 / 22 PASS (100%).** Governing-intent priors were
additionally re-resolved this session: E-18 `00_priors.md:184-197`, E-19
`:198-213`, E-25 `:276-284`, E-27 `:298-310` -- all present at the cited
ranges with the cited content.

**Nuance on row 17 (not a miss, recorded openly, does not reopen #3).** The
Symptom #3 grep block (`05_symptoms.md:752-755`) enumerates "6 consumers" and
the live grep this session returns those 6 plus `app/models/__init__.py`. That
seventh hit is the models-package barrel (re-export aggregator); it is
provably not a settle / transfer / recurrence / status-transition module and
performs no logic. The load-bearing claim under test -- "no settle module
imports `LoanParams`; the sole post-creation writer is `update_params`
`setattr`" -- resolves to source exactly (rows 13, 14, 17). Symptom #3's own
12-module settle/status enumeration (`05_symptoms.md:756-761`) is the relevant
set and none of those 12 appears. The 6-vs-7 difference is the symptom's grep
filtering only `app/models/loan_params.py` while the barrel re-exports it;
this is a transparency note on the symptom's grep reproduction, not a
falsification of the finding. Per the gate's falsification rule the miss
threshold concerns the claim being verified; that claim (zero settle-writer)
holds at 100%. **No symptom reopened.**

### 2. Completeness reconciliation

Section-3 schema elements per symptom (S = Symptom in developer's words; R =
Reproduction path; T = Hypothesis tree, every node file:line Read this
session; L = Per-branch finding linkage; W = hand-recomputed Worked example
with intermediate Decimal steps; B = Best-evidence root cause vs governing
E-NN with remediation direction; I = Independent-vs-inherited note; V =
Verification plan):

| Symptom | S | R | T | L | W | B | I | V | Tree nodes carry this-session citation? |
|---|---|---|---|---|---|---|---|---|---|
| #1 | yes `:20-26` | yes `:28-41` | yes `:43-122` | yes `:124-193` | yes `:195-281` | yes `:283-323` | yes `:325-350` | yes `:352-388` | yes (every G/S node cites a re-read `file:line`) |
| #2 | yes `:394-402` | yes `:404-416` | yes `:418-489` | yes `:491-521` | yes `:523-606` | yes `:608-639` | yes `:641-657` | yes `:659-681` | yes |
| #3 | yes `:687-694` | yes `:696-704` | yes `:706-766` | yes `:768-789` | yes `:791-817` | yes `:819-845` | yes `:847-855` | yes `:857-878` | yes |
| #4 | yes `:884-890` | yes `:892-900` | yes `:902-926` | yes `:928-947` | yes `:949-983` | yes `:985-1009` | yes `:1011-1023` | yes `:1025-1045` | yes |
| #5 | yes `:1094-1099` | yes `:1101-1134` | yes `:1136-1229` | yes `:1231-1314` | yes `:1316-1435` | yes `:1437-1481` | yes `:1483-1517` | yes `:1519-1560` | yes |

All 5 symptoms carry all 8 schema elements. The shared cross-symptom
"do #2/#3/#4 collapse onto one column" determination is present
(`:1049-1088`) as required by the P5-b stop condition.

**`NO-FINDING -> RE-INVESTIGATE` enumeration (audit-plan 5 / contract item
5).** Every symptom explicitly enumerates this branch and the count is
**zero** in all five, each with a stated reason that the AGREE nodes were
independently re-confirmed at source this session (not assumed):

- #1 `:168-172` -- "No NO-FINDING branch requires RE-INVESTIGATE: every
  divergent node maps to an existing Phase-3 finding, and every AGREE node was
  independently confirmed at source this session."
- #2 `:513-521` -- STRATEGY/SAVINGS, rate axis, quantization each AGREE with
  an explicit "No RE-INVESTIGATE" and the in-window rate reason (Q-23/A-23).
- #3 `:787-789` -- "No NO-FINDING -> RE-INVESTIGATE: every node maps to
  F-014/F-015/F-016 and every AGREE/absence node was independently grep- or
  read-confirmed this session."
- #4 `:940-947` -- rate axis AGREE with explicit "No RE-INVESTIGATE".
- #5 `:1302-1314` -- "No NO-FINDING -> RE-INVESTIGATE branch: every divergent
  node maps to F-001 or F-008; every AGREE node was independently confirmed at
  source."

Zero re-investigation branches is itself the recorded audit of Phase 3's
completeness against these five symptoms: every divergent node mapped to an
existing F-ID, so no Phase-3 gap was discovered. This is stated as a
conclusion, not an omission.

### 3. Independent-vs-inherited roll-up (vs C2 `03_consistency.md:6049-6060`, drift register `04_source_of_truth.md:2129-2146`)

| Symptom | C2 row (re-read `:6053-6057`) | Drift row (re-read `:2138-2142`) | P5 independent verdict | Contradiction? |
|---|---|---|---|---|
| #1 | F-009 + W-277 | F-002/F-003/F-001; blocking Q-16, **Q-20** | **CONFIRM + NARROW**: sole driver = `selectinload` asymmetry (rows 3-7 above); anchor-None (Q-16/Q-20 -> E-19) is real but **not causal** for $160/$114.29 (anchor SET -> `if account else` and `or` resolve identical) | No -- scope narrowing; F-002/F-003/F-009 verdicts unchanged |
| #2 | F-013 | F-013 + F-026; blocking Q-22, Q-23, Q-17 | **CONFIRM + NARROW**: F-013 `n`-axis + F-026 monthly creep, both on frozen `current_principal`. Narrowing of F-013's worked reconstruction (`03_consistency.md:1116-1119` "off by ~1"): proven `max_months = params.term_months = T` (row 11), so site-3 `n` = site-7 `n` **or** `+1` (gap 0 or 1, day-alignment dependent), not invariably +1 | No -- determinacy sharpened; F-013 DIVERGE verdict + root attribution unchanged |
| #3 | F-014 | F-014/F-015/F-016; blocking Q-22 | **CONFIRM + STRENGTHEN**: zero `.current_principal=` writes (row 14); settle modules do not import `LoanParams` (row 17) -- a stronger statement than "no writer," matching `04_source_of_truth.md:673-676` | No |
| #4 | F-026 | F-026 vs E-02/W-048; blocking Q-17, Q-22, Q-23 | **CONFIRM**: inert ARM-window columns (row 18) + frozen P re-amortized over shrinking n. Immaterial sub-dime arithmetic note ($2,460.45 here vs $2,460.50 at `04_source_of_truth.md:1129`) from hand-precision of `(1.005)^336`; not a verdict discrepancy | No |
| #5 | F-001 + F-008 | F-001/F-003/F-008/F-015/F-016; blocking Q-16, **Q-20**, Q-22 | **CONFIRM + SHARPEN**: A=D (entry formula) vs B=C=E (effective_amount); 4-way anchor-None; 3-way loan base. Sharpening: "matches nowhere" is a **two-cluster split** for the anchor-SET checking facet (`/accounts` matches `/savings` + net-worth, mismatches grid + dashboard); literal "nowhere" holds only in the anchor-None facet. Q-15 dual-dispatcher re-classified governed (subsumed by E-18/E-19 single resolver), not blocked | No -- mismatch cardinality made precise; F-001/F-008 DIVERGE verdicts unchanged |

**No Phase-5 finding contradicts a prior-phase verdict.** Three independent
re-derivations *narrow/sharpen/strengthen* the inherited map (#1 anchor-None
de-scoped from the reported numbers; #2 off-by-one determinacy; #3
import-level strengthening; #5 mismatch-cardinality). All narrowings are
scope refinements with both citations shown; no DIVERGE/SOURCE_DRIFT/
SILENT_DRIFT verdict from Phase 3 or Phase 4 is overturned. The C2 assertion
that #2/#3/#4 collapse onto one un-maintained column is CONFIRMED
(`05_symptoms.md:1051-1088`), narrowed only by naming F-013's contribution to
#2 as the same-root multi-surface `n` axis rather than an independent defect.

### 4. Acceptance gate (phase5_plan.md section 5, G1-G9)

| Gate | Criterion | Evidence | Verdict |
|---|---|---|---|
| **G1** | `05_symptoms.md` non-empty; one subsection per #1-#5, every section-3 element | Section 2 table above: 5 symptoms x 8 elements all present; file is 1562+ lines | **PASS** |
| **G2** | Every tree node cites `file:line` Read during a Phase 5 session | Section 2 last column (all yes); spot-check rows 1-22 re-resolved this session | **PASS** |
| **G3** | Worked examples arithmetic-consistent, hand-recomputed, Decimal steps shown; developer figures reproduced or discrepancy explained | #1 gap $160-$114.29=$45.71=cleared_debit (`:262-281`); #2 same-day $2.53 spread + monthly +$2.54 creep + after-edit sign (`:548-606`), reconciled to non-monotone $1911/$1914/$1912 per `04_source_of_truth.md:1150-1154`; #4 $2,460.45->$2,463.28 vs constant $2,398.20 (`:963-983`). Absolute cents flagged illustrative where account-3 params are not in-repo and app not run (contract item 6, `:525-529`); mechanism/sign/shape are the pinned facts | **PASS** |
| **G4** | Every tree branch maps to a Phase-3 F-ID or is RE-INVESTIGATE with reason | Per-branch linkage present each symptom; RE-INVESTIGATE count = 0, each justified (Section 2 enumeration) | **PASS** |
| **G5** | Spot-check >= 15 claims, 100% resolve, table + count shown | Section 1: 22 claims, 22 PASS (100%), table shown | **PASS** |
| **G6** | Each best-evidence root cause stated vs governing E-NN with remediation direction; no symptom verdict blocked | #1 -> E-25/E-19/E-27 (`:283-323`); #2 -> E-18 (`:608-639`); #3 -> E-18/A-22 (`:819-845`); #4 -> E-18/A-17 (`:985-1009`); #5 -> E-18/E-19/E-25 (`:1437-1481`). All Q-08..Q-26 answered (E-18..E-28); no axis left blocked (Q-15 subsumed, recorded governed) | **PASS** |
| **G7** | Independent-vs-inherited note for all 5; any C2/drift contradiction surfaced as a finding | Section 3 roll-up: all 5 present; zero contradictions; narrowings/sharpenings recorded with both citations | **PASS** |
| **G8** | No new auditor-invented "obvious" expectation added to `09_open_questions.md` | No `09_open_questions.md` edit this phase (`git status` row below shows only `05_symptoms.md`); no new Q proposed; only already-locked E-18..E-28 consumed | **PASS** |
| **G9** | `git status` shows only `docs/audits/financial_calculations/`; no source/test/migration/template/JS touched | `git status --short` -> `?? docs/audits/financial_calculations/05_symptoms.md` (sole entry) | **PASS** |

**G1-G9: 9 / 9 PASS.**

### 5. Handoff to Phases 6-9

- **Phase 6 (DRY/SOLID).** The structural root across all 5 symptoms is the
  absence of canonical producers: one entries-aware balance/subtotal producer
  (E-25), one date-anchored anchor resolver (E-19), one event-derived loan
  resolver (E-18). Concrete DRY targets carried: the silent
  `'entries' not in txn.__dict__` fallback (`balance_calculator.py:353-354`);
  the dual per-account dispatcher (`savings_dashboard_service.py:294` vs
  `year_end_summary_service.py:2036`); >=4 surfaces each assembling their own
  `(P,r,n)` for `calculate_monthly_payment`; the grid subtotal on raw
  `effective_amount` (`grid.py:263-279`). E-26 (out of symptom scope but
  recorded in priors) flags the 19-file `TWO_PLACES` redeclaration + 24
  banker's-rounding sites as the Phase-6 rounding-helper root.
- **Phase 7 (test gaps).** Each symptom's Verification plan item 5 states an
  exact post-remediation equivalence assertion (the E-04 invariant per
  symptom) -- these are the regression targets Phase 7 must convert to tests:
  cross-page balance equality (#1, #5), cross-surface + cross-date payment
  equality inside the ARM window (#2, #4), strict principal decrease per
  settled transfer (#3). Each symptom also carries an explicit falsification
  test (the "if it does not, re-investigate" clause) Phase 7 should encode as
  the negative control.
- **Phase 8 (findings).** Best-evidence root causes are stated as cited
  hypotheses, not severities. The C3 CRITICAL pre-list
  (`03_consistency.md:6062+`) is Phase 8's severity input; Phase 5 adds no
  severity. Symptoms #2/#3/#4 collapse onto one column (`05_symptoms.md:
  1049-1088`) -- Phase 8 should treat them as one finding family with one
  remediation (E-18 single resolver), not three independent fixes.
- **Phase 9 (open questions).** No new open question raised this phase (G8).
  All Q-08..Q-26 consumed as locked E-18..E-28. The only carried tail is A-26
  (next bullet).
- **A-26 out-of-symptom-scope carry (recorded, NOT dropped).** E-18
  (`00_priors.md:184-197`, re-read this session: "`auth.user_settings.
  estimated_retirement_tax_rate` remains a pure user input with no mirror ...
  its NULL-fallback contract is a separate question and is not decided by
  E-18") does **not** decide the `estimated_retirement_tax_rate`
  NULL-semantics contract. It touches no balance or loan symptom (#1-#5) and
  is therefore out of Phase-5 symptom scope. It is **carried forward to Phase
  9 / the Q-26 reconciliation track as an explicitly open NULL-semantics
  question**, not silently lost. phase5_plan.md section 0 (`:28-32`) and
  section 4 P5-d task 5 require this carry; it is hereby recorded.

### 6. `git status` (confirming only audit docs changed)

```
$ git status --short
?? docs/audits/financial_calculations/05_symptoms.md
```

Sole entry. No source, test, migration, template, or JS file touched. G9 PASS.

### Phase 5 completion

**Phase 5 complete.** Gate roll-up: **G1 PASS, G2 PASS, G3 PASS, G4 PASS,
G5 PASS (22/22, 100%), G6 PASS, G7 PASS, G8 PASS, G9 PASS -- 9/9.**
Spot-check 22/22 at source; all 5 symptoms carry every section-3 element with
this-session citations; zero `NO-FINDING -> RE-INVESTIGATE` branches (each
justified); independent re-derivation confirms the C2 map and drift register
with documented narrowings/sharpenings and **zero contradictions** of any
prior-phase verdict; #2/#3/#4 independently re-proven to collapse onto the one
un-maintained `current_principal` column under E-18; A-26
`estimated_retirement_tax_rate` NULL-semantics tail carried to Phase 9, not
dropped. No gate failed; no symptom reopened.

---
