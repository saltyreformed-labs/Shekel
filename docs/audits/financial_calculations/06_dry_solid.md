> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# 06 - DRY and SOLID Audit

Phase 6 output. Authoritative spec: `financial_calculation_audit_plan.md`
section 6 (lines 614-676). Execution contract: `phase6_plan.md` sections 2-3.
Read-only audit, `plan` permission mode. Findings only; no fix is written.

This file accumulates across Phase 6 sessions:

- Part 6.1 DRY -- canonical-producer-absence family (P6-a1, this section).
- Part 6.1 DRY -- cross-cutting micro-duplications (P6-a2, later).
- Part 6.2 SOLID (P6-b, later).
- Part 6.3 Boundary (P6-c, later).
- Verification and consolidation gate (P6-d, later).

---

# Part 6.1 DRY -- canonical-producer-absence family

Scope (P6-a1): the figures E-18 (loan resolver), E-19 (anchor resolver), E-24
(loan-obligation aggregator + 26/12 factor), E-25 (period subtotal), E-27
(balance-as-of-date), and the F-010 `_sum_remaining` / `_sum_all` split. One
`D6-NN` per root. Every site below was grepped or Read in session P6-a1; every
function cited was Read in full this session before the conclusion was drawn.

Principle source for every DRY finding in this part: `financial_calculation_
audit_plan.md:623-642` ("List every case where the same calculation appears in
two or more places ... recommend a single source of truth"), and
`docs/coding-standards.md` Python "DRY and SOLID" ("Do not duplicate logic --
extract shared behavior ... Verify equivalent logic does not already exist
before writing new code").

---

## D6-01 -- No single loan resolver: every surface re-assembles its own `(principal, rate, n)` for `calculate_monthly_payment`

**Principle.** DRY (`financial_calculation_audit_plan.md:623-642`;
coding-standards "DRY and SOLID"). The single annuity formula is correctly
written once; the duplication is the *input-triple assembly* repeated at every
call site, each choosing its own principal source, rate source, and term `n`.

**Grep (this session).** `grep -rn "calculate_monthly_payment(" app/` -- 17
hits: 1 definition + 16 call sites. All 16 call sites pasted (the F-013
16-site register, re-resolved at live source this session):

```
app/services/amortization_engine.py:178   def calculate_monthly_payment(   [the one formula]
app/services/loan_payment_service.py:251   compute_contractual_pi -- ARM branch
app/services/loan_payment_service.py:256   compute_contractual_pi -- fixed branch
app/routes/debt_strategy.py:127            minimum_payment (real_principal, stored rate, calendar remaining)
app/services/amortization_engine.py:436    generate_schedule -- using_contractual (original_principal, term_months)
app/services/amortization_engine.py:440    generate_schedule -- re-amort (current_principal, remaining_months)
app/services/amortization_engine.py:491    generate_schedule -- anchor reset (anchor balance, months_left loop index)
app/services/amortization_engine.py:512    generate_schedule -- rate-change re-amort (RateHistory rate, months_left)
app/services/amortization_engine.py:693    calculate_payoff_scenarios -- contractual branch
app/services/amortization_engine.py:697    calculate_payoff_scenarios -- re-amort branch
app/services/amortization_engine.py:952    get_loan_projection -- ARM (current_principal, stored rate, remaining)
app/services/amortization_engine.py:957    get_loan_projection -- fixed (orig_principal, stored rate, term_months)
app/routes/loan.py:1102                     refinance preview (form/derived principal)
app/routes/loan.py:1225                     loan->checking transfer default -- ARM branch
app/routes/loan.py:1231                     loan->checking transfer default -- fixed branch
app/services/balance_calculator.py:225      ARM branch -- DEAD per F-013/F-017
app/services/balance_calculator.py:231      fixed branch -- DEAD per F-013/F-017
```

**Sites -- expanded comparison.** The same `(principal, rate, n)` decision is
re-implemented, with different choices, at independent sites. Read in full this
session:

- `amortization_engine.py:430-442` -- `using_contractual = original_principal
  is not None and term_months is not None and not has_rate_changes`; if true
  `calculate_monthly_payment(original_principal, annual_rate, term_months)`,
  else `calculate_monthly_payment(current_principal, annual_rate,
  remaining_months)`.
- `amortization_engine.py:691-699` (`calculate_payoff_scenarios`) -- the SAME
  decision rewritten: `if original_principal is not None and not
  has_rate_changes: calculate_monthly_payment(original_principal, annual_rate,
  term_months) else calculate_monthly_payment(current_principal, annual_rate,
  remaining_months)`.
- `amortization_engine.py:950-959` (`get_loan_projection`) -- the SAME decision
  a third time, keyed on the `is_arm` *column* instead of `original is not
  None`: `if is_arm and remaining > 0: calculate_monthly_payment(
  current_principal, rate, remaining) else calculate_monthly_payment(
  orig_principal, rate, params.term_months)`.
- `loan_payment_service.py:247-259` (`compute_contractual_pi`) -- the SAME
  decision a fourth time, again keyed on `is_arm`: ARM ->
  `calculate_monthly_payment(current_principal, interest_rate, remaining)`;
  else `calculate_monthly_payment(original_principal, interest_rate,
  term_months)`.
- `loan.py:1221-1235` (loan->checking transfer default) -- the SAME `is_arm`
  branch a fifth time, principal/rate/term assembled inline from `params`.
- `balance_calculator.py:220-235` -- the SAME `is_arm` branch a sixth time
  (this pair is the F-013 site 9/10 / F-017 DEAD site: the computed
  `monthly_payment` here is never consumed by the displayed value, but the
  triple-assembly duplication is live code).
- `debt_strategy.py:109-129` -- assembles `principal = current_principal`,
  `rate = interest_rate`, `remaining = calculate_remaining_months(...)`, then
  `_compute_real_principal` to replay confirmed payments, then
  `calculate_monthly_payment(real_principal, rate, remaining)` -- a *seventh*
  principal definition (confirmed-payment-reduced) found nowhere else.

Each block independently decides (a) `original_principal` vs
`current_principal` vs `real_principal`, (b) stored `interest_rate` vs
RateHistory `period_rate` (`amortization_engine.py:499-514`), and (c)
`term_months` vs `calculate_remaining_months(...)` vs the `months_left =
max_months - month_num + 1` loop index (`amortization_engine.py:490,511`).
There is no function that takes a loan and returns its authoritative
`(balance, monthly_payment, schedule)`; the decision is re-derived per surface.

**Governing E-NN.** E-18 (`00_priors.md:184-197`): "One pure resolver derives
the triple (balance, monthly_payment, schedule) from these events by replaying
confirmed payments forward from the most recent anchor. Every read surface
obtains these values only through that resolver ... and every monthly-payment
call site. No surface reads a hand-maintained mirror."

**Recommended single source of truth (report only).** One event-derived loan
resolver that, given a loan's input events (origination terms, confirmed
payments, RateHistory entries, dated anchors), returns the authoritative
`(balance, monthly_payment, schedule)` triple by replaying confirmed payments
forward from the latest anchor (origination as the implicit first anchor; ARM
as the non-special replay-from-latest-anchor case, per E-18). Every one of the
16 sites above calls that resolver instead of re-assembling its own triple;
`debt_strategy`'s `_compute_real_principal` replay collapses into it.

**Inherited-vs-independent.** Carried from `phase6_plan.md` section 1 (Loan
resolver row, E-18) and from the Phase-3 F-013 16-site register
(`03_consistency.md:1009-1148`) and the `04_source_of_truth.md:486-487`
Phase-3/Phase-6 cross-ref. **Confirmed at source, count narrowed up:** the
section-1 row says ">=4 surfaces"; the live grep returns **16 call sites in 6
files** (7 distinct input-triple definitions). The carry undercounts; the live
count is recorded here as the divergence-against-prior-phase remedy
(`phase6_plan.md` section 1, audit-plan 10.8). The two
`balance_calculator.py:225,231` sites are confirmed DEAD per F-013 site 9/10 /
F-017 but are live duplicated *code*.

**Blast radius.** The displayed mortgage "Monthly P&I" drifts across the loan
dashboard, the amortization schedule rows, `/savings`, debt-strategy, and the
loan->checking transfer default for the same loan -- the numerically observed
$1911.54 / $1914.34 / $1912.94 -> $1910.95 of symptom #2, recorded in
`03_consistency.md` F-013 (DIVERGE/SILENT_DRIFT) and the
`04_source_of_truth.md:2139` drift register (symptom #2 row). Inherits into
symptom #4 (F-026) by the same root.

---

## D6-02 -- No single anchor resolver: five consumers interpret a missing anchor five different ways

**Principle.** DRY (`financial_calculation_audit_plan.md:623-642`;
coding-standards "DRY and SOLID"). The "what anchor (balance, period) does this
account project from" decision is re-implemented per consumer, and the
implementations disagree on the missing-anchor input.

**Grep / sites (this session).** The anchor-base expression at each balance
producer, Read in full:

```
app/routes/grid.py:238-241                 anchor = current_anchor_balance if account else 0.00;
                                           anchor_period = current_anchor_period_id if account else current_period.id  (passes None when column NULL)
app/routes/accounts.py:1418-1421           anchor = current_anchor_balance or 0.00;  period ... or current_period.id  (F-001 path B)
app/services/savings_dashboard_service.py:325-328   anchor = acct.current_anchor_balance or Decimal("0.00");
                                           anchor_period_id = acct.current_anchor_period_id or (current_period.id if current_period else None)
app/services/dashboard_service.py:683-684  return None when current_anchor_period_id is None  (account omitted)
app/services/year_end_summary_service.py:2065-2066  return None when current_anchor_period_id is None  (account omitted)
app/services/calendar_service.py:449-450   return Decimal("0") when current_anchor_period_id is None
```

(`grid.py:238-241`, `savings_dashboard_service.py:325-328`,
`calendar_service.py:449-450`, `year_end_summary_service.py:2065-2066` Read in
full this session; `accounts.py:1418-1421`, `dashboard_service.py:683-684`
carried verbatim from F-001 paths B/D, both re-confirmed against the F-001
citation block `03_consistency.md:125-189`.)

**Sites -- expanded comparison.** One input -- an account whose
`current_anchor_period_id IS NULL` -- five behaviors:

- `grid.py:239-241`: passes `None` -> `calculate_balances` matches no anchor
  period -> `balances` empty -> grid renders a **blank row**
  (`balance_calculator.py:69-86`: `running_balance` stays `None`, period
  skipped).
- `accounts.py:1419-1421` / `savings_dashboard_service.py:326-328`: `... or
  current_period.id` -> falls back to the **current period seeded with the
  stored balance** -> a populated projection.
- `dashboard_service.py:683-684` / `year_end_summary_service.py:2065-2066`:
  `return None` -> the **account is omitted entirely**.
- `calendar_service.py:449-450`: `return Decimal("0")` -> a **$0.00** figure.

`balance_calculator.calculate_balances` (Read in full this session,
`:69-86`) consumes whatever `anchor_period_id` it is handed; it has no opinion
on the NULL case -- the decision lives entirely in the five callers, none
designated canonical.

**Governing E-NN.** E-19 (`00_priors.md:198-212`): "The effective anchor pay
period is resolved on read by one resolver -- the first pay period whose range
contains or follows `anchor_date` -- which every balance producer calls: grid,
`/accounts`, `/savings`, dashboard, net worth, and the calendar month-end ...
shown uniformly, not as four different representations (blank /
stored-balance-at-current-period / omitted / `$0.00`)."

**Recommended single source of truth (report only).** One date-anchored anchor
resolver: account creation establishes `(current_anchor_balance, anchor_date)`
atomically plus a t0 `AccountAnchorHistory` row, so the NULL state is
unreachable; the effective anchor period is resolved on read (first pay period
whose range contains or follows `anchor_date`). Every one of the six producers
above calls that resolver; no per-page NULL fallback remains. Consistent with
E-19 exactly as stated; no new target invented.

**Inherited-vs-independent.** Carried from `phase6_plan.md` section 1 (Anchor
resolver row, E-19) and Phase-3 F-001 (`03_consistency.md:153-189`,
anchor-None "four different behaviors") / the Phase-5 handoff
(`05_symptoms.md:1704-1705`). **Confirmed and narrowed up:** F-001 enumerated
*four* behaviors across grid/accounts/savings/dashboard/net-worth; this session
adds the **fifth** -- `calendar_service.py:449-450` returns `Decimal("0")`, a
distinct fifth representation F-001 did not include in its four-way list
(F-001's compared set was A-E; calendar is E-27's path, not in F-001's pairs).
Recorded as a Phase-6 narrowing of the carried register.

**Blast radius.** For an account in the (currently reachable) NULL-anchor-
period state, the same account shows a blank grid row, a populated `/accounts`
and `/savings` projection, omission from the dashboard and net worth, and
`$0.00` on the calendar -- the symptom-#5 "`/accounts` matches nothing" and
symptom-#1 anchor axis, recorded in `03_consistency.md` F-001
(DIVERGE/SCOPE_DRIFT) and the `04_source_of_truth.md:2138,2142` drift register
(symptom #1 and #5 rows, Q-16/Q-20).

---

## D6-03 -- No single period-subtotal producer: the grid recomputes the subtotal inline on a different expense base than the balance calculator

**Principle.** DRY (`financial_calculation_audit_plan.md:629-630`: "A service
computes a concept; a template recomputes a closely related concept" -- here a
route recomputes the balance calculator's per-period income/expense aggregation
inline). The per-period subtotal is the same financial concept as the balance
delta but is produced a second time, on a different base.

**Grep / sites (this session).** `grep -n "subtotal" app/routes/grid.py`
returns the inline block `:260-279`, `:345` (passed to template). Read in full
this session:

- `grid.py:260-279` -- inline per-period subtotal:
  ```
  for period in periods:
      income = Decimal("0"); expense = Decimal("0")
      for txn in txn_by_period.get(period.id, []):
          if txn.is_deleted or txn.status_id != projected_id: continue
          if txn.is_income:   income  += txn.effective_amount
          elif txn.is_expense: expense += txn.effective_amount
      subtotals[period.id] = {"income": income, "expense": expense,
                              "net": income - expense}
  ```
- `balance_calculator.py:403-419` (`_sum_remaining`) / `:436-451`
  (`_sum_all`) -- the balance pass over the *same* per-period transactions:
  ```
  for txn in transactions:
      if txn.status_id != projected_id: continue
      if txn.is_income:    income   += txn.effective_amount
      elif txn.is_expense: expenses += _entry_aware_amount(txn)
  ```

**Expanded comparison.** Status filter (`status_id != projected_id`) and income
term (`txn.effective_amount`) are identical between the two. The **expense term
differs**: grid uses raw `txn.effective_amount` (`grid.py:274`); the balance
calculator uses `_entry_aware_amount(txn)` (`balance_calculator.py:417,449`),
which for a Projected envelope expense with loaded entries returns
`max(estimated - cleared_debit - sum_credit, uncleared_debit)`
(`balance_calculator.py:383-386`, Read in full this session) instead of
`effective_amount`. Two producers of one concept, divergent on exactly the
entry-aware expense base. (`grid.py` additionally short-circuits on
`txn.is_deleted` inline, but the grid query already filters
`is_deleted=False`, so that is not the divergence axis.)

**Governing E-NN.** E-25 (`00_priors.md:276-284`): "It is produced by ONE
service function that every consumer reads; the route does not compute it
inline. Its expense base is `_entry_aware_amount` -- the same base
`balance_calculator._sum_remaining` / `_sum_all` use -- so the subtotal net
reconciles with the running-balance delta on the same grid by construction."

**Recommended single source of truth (report only).** One period-subtotal
producer in the service layer, sharing `_entry_aware_amount` with the balance
calculator (ideally computed in the same pass as `calculate_balances` so
`balance[p] - balance[p-1]` equals `subtotal.net` by construction); the grid
route reads it instead of recomputing inline on `effective_amount`. Exactly
E-25's stated consolidation; no new target invented.

**Inherited-vs-independent.** Carried from `phase6_plan.md` section 1 (Period
subtotal row, E-25) and the Phase-5 handoff `05_symptoms.md:1710-1711` ("the
grid subtotal on raw `effective_amount` (`grid.py:263-279`)"). **Confirmed at
source**; the live block is `grid.py:260-279` (the carried `:263-279` is the
inner-loop subset; `:260-262` is the comment header, `:264` the `subtotals`
dict init -- same construct, line range widened by 3 and recorded).

**Blast radius.** For any checking account with a Projected envelope expense
that has cleared/credit entries, the grid's displayed per-period net subtotal
does not reconcile with the running-balance delta on the same screen
(`balance[p]-balance[p-1] != subtotal.net`) -- the F-004 grid-subtotal-vs-
balance facet of symptom #1, cross-linked by Q-10 in F-009
(`03_consistency.md:722-723`).

---

## D6-04 -- No single "balance as of date" path: the calendar month-end is a second, non-entries-aware balance path

**Principle.** DRY (`financial_calculation_audit_plan.md:628-629`: "Two
services compute the same concept with copied formulas"). "Projected balance as
of a date" exists as the grid/`/savings`/dashboard `calculate_balances` path
and, separately, as the calendar's own period-selection + query + slice path
with a different entries-load contract.

**Grep / sites (this session).** `grep -n` for the month-end path in
`calendar_service.py`; `_compute_month_end_balance` Read in full
(`calendar_service.py:435-489`):

- `calendar_service.py:449-450` -- `if account.current_anchor_period_id is
  None: return Decimal("0")` (the D6-02 fifth anchor behavior).
- `calendar_service.py:461-466` -- period selection: "Find the last period
  whose `end_date <= last_day` of month" (`for p in all_periods: if
  p.end_date <= last_day: target_period = p`).
- `calendar_service.py:471-480` -- the transaction query: filters
  `account_id`, `scenario_id`, `pay_period_id.in_(period_ids)`,
  `is_deleted.is_(False)`, **no `selectinload(Transaction.entries)`**.
- `calendar_service.py:482-489` -- `balance_calculator.calculate_balances(
  anchor_balance, anchor_period_id, all_periods, all_txns)`, then
  `balances.get(target_period.id, Decimal("0"))`.

Contrast the entries-aware producers, Read in full this session:
`grid.py:229` selectinloads entries before `calculate_balances`
(`grid.py:243`); `dashboard_service.py:689` likewise (per F-009
`03_consistency.md:660-668`).

**Expanded comparison.** Both ultimately call
`balance_calculator.calculate_balances`, but the *date->balance* wrapper is
duplicated and divergent on two axes: (1) **period selection** -- the calendar
returns the end balance of "the last period ending on or before month-end"
(`calendar_service.py:461-466`), a value up to ~13 days stale versus the actual
calendar month-end, not a true balance-as-of-date; (2) **entries load** -- the
calendar query omits `selectinload(Transaction.entries)`
(`calendar_service.py:471-480`), so `_entry_aware_amount` short-circuits at
`'entries' not in txn.__dict__` (`balance_calculator.py:353-354`, Read this
session) and the calendar holds back full `estimated_amount` while the grid (
entries loaded) holds back the entry-aware remainder -- the F-003 / F-009
SILENT_DRIFT mechanism, here on a second code path.

**Governing E-NN.** E-27 (`00_priors.md:298-308`): "This figure is produced
through the single canonical 'balance as of date D' capability, entries-aware,
sharing the same expense base as `balance_calculator` and the canonical
period-subtotal producer (E-25) ... routing through the canonical path removes
that drift by construction. The anchor-None short-circuit at
`calendar_service.py:449-450` ... is subsumed by E-19."

**Recommended single source of truth (report only).** One canonical
entries-aware "balance as of date D" path, anchor-resolved per E-19 and sharing
`_entry_aware_amount` with the balance calculator and the E-25 subtotal
producer; the calendar month-end is that path evaluated at the calendar
month-end date (with the documented per-transaction effective-date rule E-27
flags as the open implementation detail). The `calendar_service.py:449-450`
zero fallback is removed (subsumed by E-19). Exactly E-27's stated target; the
effective-date derivation is E-27's explicitly-open implementation detail, not
an auditor-invented target.

**Inherited-vs-independent.** Carried from `phase6_plan.md` section 1
(Balance-as-of-date row, E-27) and F-003 (`03_consistency.md:283-339`) / F-009
(`:649-725`) entries-load divergence. **Confirmed at source:** the no-
`selectinload(entries)` gap at `calendar_service.py:471-480` and the period-
selection slice at `:461-466` resolve exactly as E-27's proof substrate
(`00_priors.md:300-302,308`) states.

**Blast radius.** The calendar's "End Balance" both (a) misrepresents -- it is
a days-stale period-end balance, not the month-end balance -- and (b) diverges
from the grid/`/savings` for the same account and date because it never loads
entries; the F-003/F-009 SILENT_DRIFT and the W-277/Q-18 period-selection axis,
recorded in `03_consistency.md` F-003 (DIVERGE/SILENT_DRIFT) and the E-27
verification direction (`00_priors.md:310`).

---

## D6-05 -- No single loan-obligation / committed-monthly aggregator; the 26/12 biweekly-to-monthly factor is redeclared at four sites

**Principle.** DRY (`financial_calculation_audit_plan.md:628-630`: copied
formulas across services; a service computes a concept and another recomputes a
closely related one). Two duplications under one root: (a) the
template-monthly-equivalent aggregation loop, and (b) the 26/12 conversion
constant.

**Grep / sites (this session).**

(a) The aggregation loop. `obligations.py:331-395` Read in full -- three
structurally-identical loops:

```
app/routes/obligations.py:331-350   expense_items   loop
app/routes/obligations.py:352-372   income_items    loop
app/routes/obligations.py:374-395   transfer_items  loop
app/services/savings_goal_service.py:287-328   compute_committed_monthly  (parallel loop)
```

Each `obligations.py` loop body: `rule = tmpl.recurrence_rule`; `if
rule.pattern_id == once_id: continue`; `if rule.end_date is not None and
rule.end_date < date.today(): continue`; `monthly = amount_to_monthly(amount,
rule.pattern_id, rule.interval_n)`; `if monthly is None: continue`; `total +=
monthly`; append a per-item dict with `monthly.quantize(TWO_PLACES,
ROUND_HALF_UP)`. `compute_committed_monthly` (`savings_goal_service.py:308-328`)
runs the same accumulation over `expense_templates + transfer_templates` and
calls the same `amount_to_monthly` -- **but skips only ONCE (via
`amount_to_monthly` returning `None`) and zero/no-rule; it does NOT apply the
`rule.end_date < date.today()` filter** that all three `obligations.py` loops
apply (`obligations.py:335-336,358-359,380-381`).

(b) The 26/12 factor. `grep -rn "26 / 12" "Decimal(\"26\")"
PAY_PERIODS_PER_YEAR _MONTHS_PER_YEAR app/`:

```
app/services/savings_goal_service.py:17-18   _PAY_PERIODS_PER_YEAR = Decimal("26"); _MONTHS_PER_YEAR = Decimal("12")   [the named constants]
app/services/savings_goal_service.py:95       net_biweekly_pay * _PAY_PERIODS_PER_YEAR / _MONTHS_PER_YEAR
app/services/savings_goal_service.py:169      months * Decimal("26") / Decimal("12")        [inline, not the constant -- same file]
app/services/savings_goal_service.py:265      amount * _PAY_PERIODS_PER_YEAR / _MONTHS_PER_YEAR
app/services/savings_goal_service.py:269      amount * _PAY_PERIODS_PER_YEAR / n / _MONTHS_PER_YEAR
app/services/savings_goal_service.py:281      amount / _MONTHS_PER_YEAR
app/services/savings_dashboard_service.py:171  gross_biweekly * Decimal("26") / Decimal("12")   [inline literal]
app/services/savings_dashboard_service.py:765  per_period * Decimal("26") / Decimal("12")        [inline literal]
app/services/retirement_gap_calculator.py:69    net_biweekly_pay * 26 / 12                        [inline int literals]
```

Named once at `savings_goal_service.py:17-18`; re-inlined as `Decimal("26") /
Decimal("12")` at `savings_dashboard_service.py:171,765`, as bare `26 / 12` at
`retirement_gap_calculator.py:69`, and even inline within the declaring file at
`savings_goal_service.py:169`.

**Expanded comparison.** Aggregator: the three `obligations.py` loops and
`compute_committed_monthly` are the same accumulate-monthly-equivalent
operation; expanded side by side they differ only by the `end_date < today`
guard, which `compute_committed_monthly` omits -- so an expired template
contributes to the EF baseline / per-goal floors
(`savings_dashboard_service.py:768+`, the committed-floor consumers) forever
while `/obligations` correctly drops it. Factor: `gross_biweekly *
Decimal("26") / Decimal("12")` (`savings_dashboard_service.py:171`) and
`net_biweekly_pay * _PAY_PERIODS_PER_YEAR / _MONTHS_PER_YEAR`
(`savings_goal_service.py:95`) are the identical biweekly->monthly conversion
written two ways; `retirement_gap_calculator.py:69`'s `* 26 / 12` is the same
again with int literals (a latent precision concern, but the duplication is the
finding here).

**Governing E-NN.** E-24 (`00_priors.md:260-274`): "A single canonical
monthly-equivalent aggregator replaces the three structurally-identical inline
loops in `obligations.summary` (`obligations.py:331-395`) and the parallel
logic in `compute_committed_monthly`; it applies one filter rule -- skip ONCE,
skip `end_date < today` -- shared by every consumer. ... The 26/12
biweekly-to-monthly factor is defined once (the existing `_PAY_PERIODS_PER_YEAR`
/ `_MONTHS_PER_YEAR` constants) and imported, not re-inlined at
`savings_dashboard_service.py:170-172` and `:765`."

**Recommended single source of truth (report only).** One canonical
monthly-equivalent aggregator applying the shared skip-ONCE / skip-`end_date <
today` filter, called by all three `obligations.py` loops and by
`compute_committed_monthly`; the `_PAY_PERIODS_PER_YEAR` /
`_MONTHS_PER_YEAR` constants imported at every 26/12 site
(`savings_dashboard_service.py:171,765`, `retirement_gap_calculator.py:69`, and
the in-file inline `savings_goal_service.py:169`) rather than re-inlined.
Exactly E-24's stated consolidation; no new target invented. (E-24's
distinct-question paths -- `obligations.summary` vs `dashboard._compute_cash_
runway` vs `savings_dashboard._compute_debt_summary` -- are NOT required to
agree and are out of this DRY root.)

**Inherited-vs-independent.** Carried from `phase6_plan.md` section 1
(Loan-obligation aggregator row, E-24) and the Phase-3 W-251 tag
(`03_consistency.md:5328`: "monthly equivalents use pattern-to-monthly
normalization factors ... Q-12 (aggregator-owner/SRP, Phase-6, separate)").
**Confirmed at source, factor-site count narrowed up:** E-24's proof substrate
names the 26/12 duplication at `savings_dashboard_service.py:170-172,765`; this
session additionally resolves `retirement_gap_calculator.py:69` and the in-file
`savings_goal_service.py:169` as further re-inlinings of the same factor,
recorded as a Phase-6 narrowing. The `compute_committed_monthly` missing
`end_date` filter is confirmed at `savings_goal_service.py:308-328` exactly as
E-24's "proven defect #1" states.

**Blast radius.** Expired recurring templates inflate the emergency-fund
baseline and per-goal contribution floors indefinitely on `/savings` while
`/obligations` excludes them; and a biweekly amount's monthly equivalent can
drift between `/savings`, `/obligations`, and the retirement-gap projection if
any inlined factor is edited independently -- the E-24 "defect #1" /
"one obligation, two unreconciled representations" recorded in
`03_consistency.md` F-008 (debt_total, the internal stored-vs-engine
inconsistency, `:621-627`) and the E-24 verification direction
(`00_priors.md:274`).

---

## D6-06 -- `_sum_remaining` and `_sum_all` are byte-identical bodies that differ only by docstring

**Principle.** DRY (`financial_calculation_audit_plan.md:632-635`, verbatim:
"Two helpers with similar names (`_sum_remaining`, `_sum_all`, ...) that share
most of their structure but vary by filter. These should likely share a
parameterized core."). Here the two do not even vary by filter -- they are
identical.

**Grep / sites (this session).** Both Read in full this session:

- `balance_calculator.py:389-419` -- `_sum_remaining`, body `:403-419`.
- `balance_calculator.py:422-451` -- `_sum_all`, body `:436-451`.
- Callers: `balance_calculator.py:74` (`_sum_remaining` for the anchor
  period), `:79` (`_sum_all` for post-anchor periods) -- Read in full this
  session (`calculate_balances:69-86`).

**Expanded comparison.** The two bodies, lines aligned:

```
_sum_remaining :403-419                         _sum_all :436-451
income   = Decimal("0.00")                       income   = Decimal("0.00")
expenses = Decimal("0.00")                       expenses = Decimal("0.00")
projected_id = ref_cache.status_id(             projected_id = ref_cache.status_id(
    StatusEnum.PROJECTED)                            StatusEnum.PROJECTED)
for txn in transactions:                         for txn in transactions:
    if txn.status_id != projected_id: continue       if txn.status_id != projected_id: continue
    if txn.is_income:                                if txn.is_income:
        income   += txn.effective_amount                 income   += txn.effective_amount
    elif txn.is_expense:                             elif txn.is_expense:
        expenses += _entry_aware_amount(txn)             expenses += _entry_aware_amount(txn)
return income, expenses                           return income, expenses
```

Byte-identical executable bodies. The only difference is the docstring
(`:390-401` "anchor period, items done/received already in the anchor" vs
`:423-434` "non-anchor period"). The actual anchor-vs-roll-forward distinction
is entirely in the caller `calculate_balances` (`:72-80`: anchor period seeds
`anchor_balance + income - expenses`; post-anchor seeds `running_balance +
income - expenses`), not in either helper. The helpers are period-agnostic pure
summations; the names assert a difference the code does not contain.

**Governing E-NN.** E-25 family (`00_priors.md:276-284`): the period-subtotal /
balance-base producer is one shared `_entry_aware_amount`-based summation;
`NONE -> structural-only` for the consolidation of the two helpers themselves
(no E-NN names a single-`_sum_*` target; F-010 explicitly recorded this as "a
DRY observation for Phase 6, not a Phase-3 consistency divergence",
`03_consistency.md:756`). This is a pure-structure DRY finding, not a
correctness one (the two cannot disagree -- they are the same code).

**Recommended single source of truth (report only).** Collapse to one
parameter-free summation helper called by both `calculate_balances` arms; the
anchor-vs-roll-forward semantics already live solely in the caller, so no
parameter is needed (consistent with the E-25 single-base producer; the
audit-plan's "parameterized core" reduces here to "one helper, no parameter"
because the filters are identical, not merely similar).

**Inherited-vs-independent.** Carried from `phase6_plan.md` section 1 (Helper
split row) and the Phase-3 F-010 DRY note (`03_consistency.md:727-765`,
explicitly: "`_sum_remaining` and `_sum_all` are identical and should likely
collapse to one parameter-free helper; recorded for `06_dry_solid.md`").
**Confirmed at source, sharpened:** the audit-plan example (line 632-635) and
the section-1 register both phrase this as "share most of their structure but
vary by filter"; the live source shows they do **not** vary by filter at all --
the bodies are byte-identical. Recorded as a Phase-6 sharpening of the carried
characterization (the duplication is total, not partial).

**Blast radius.** No observed numeric drift (the two are the same code, so they
cannot disagree -- F-010 verdict AGREE, `03_consistency.md:761`). Latent
structural risk only: a future edit to one body (e.g. adding a credit-status
guard to `_sum_all` but not `_sum_remaining`) would silently split the anchor
period from post-anchor periods with no test catching the divergence -- exactly
the duplication-as-drift-substrate thesis (`financial_calculation_audit_plan.
md:618`).

---

### Part 6.1 (canonical-producer-absence family) -- session P6-a1 complete

Six `D6-` roots recorded (D6-01 loan resolver / D6-02 anchor resolver / D6-03
period subtotal / D6-04 balance-as-of-date / D6-05 obligation aggregator +
26/12 / D6-06 `_sum_*` split), each with principle + citation, every grepped
site (all pasted), the expanded-form comparison, the governing E-NN, the
report-only single source of truth, the inherited-vs-independent note, and the
blast radius cross-linked to the Phase 3/4/5 finding that observed the drift.
Three carried-register counts were narrowed up against the prior phase and
recorded as such (D6-01 16 sites vs ">=4"; D6-02 fifth anchor behavior vs
F-001's four; D6-05 two extra 26/12 inlinings vs E-24's two named). The
cross-cutting micro-duplications (E-26 rounding helper, effective_amount
mirrors, inline status filter, PA-05 magic numbers) are P6-a2 scope and are not
recorded here. No fix written; no source/test/migration touched.

---

# Part 6.1 DRY -- cross-cutting micro-duplications

Scope (P6-a2): the high-count repository-wide sweeps -- E-26 (rounding-helper
absence: the per-file `TWO_PLACES` redeclaration and the bare monetary
`.quantize()` sites silently using banker's rounding), the `effective_amount`
hand-rolled mirror inventory, the inline status-filter inventory, and the PA-05
magic-number-fallback inventory. One `D6-NN` per root. Per the trust-but-verify
contract (`phase6_plan.md` section 2 item 8) every repository-wide pattern was
swept this session through the Explore subagent (one invocation per pattern,
thoroughness `very thorough`); the returned `file:line` registers are folded in
below in full. The live source lines required for the E-26 monetary-boundary vs
intermediate classification and for the sanctioned-exception exclusion were Read
in this main session (cited inline).

Principle source for every DRY finding in this part: `financial_calculation_
audit_plan.md:623-642` ("List every case where the same calculation appears in
two or more places ... recommend a single source of truth"), specifically the
inline-duplication bullets at `:636-639` (status filters inline; effective-amount
selection reproduced inline), and `docs/coding-standards.md` Python "DRY and
SOLID" ("Do not duplicate logic -- extract shared behavior ... Verify equivalent
logic does not already exist before writing new code") plus "No magic numbers or
strings" (`docs/coding-standards.md` Python "Code Structure").

---

## D6-07 -- No centralized money-rounding helper: `TWO_PLACES` is redeclared in 19 files and 24 monetary `.quantize()` sites silently use banker's rounding

**Principle.** DRY (`financial_calculation_audit_plan.md:623-642`; `docs/coding-
standards.md` Python "DRY and SOLID") and the E-26 single-helper expectation
(`00_priors.md:286-296`). E-26 verbatim (`:288`): "There is exactly one
implementation of this boundary operation: a centralized helper module ...
`round_money(x)` (2dp, ROUND_HALF_UP ...) ... the per-service redeclared
`TWO_PLACES = Decimal("0.01")` constant (present in 19 files), are replaced by
it." E-26 rationale (`:292`): bare `.quantize()` "silently default to Python's
context rounding (`ROUND_HALF_EVEN`, banker's rounding) and produce different
cents than the `ROUND_HALF_UP` convention ... every hand-computed test assertion
assume[s]."

**Grep / sites (this session, Explore sweeps; live lines Read this session for
the classification).**

### (a) The 19-file `TWO_PLACES = Decimal("0.01")` redeclaration register

Sweep: `grep -rn 'TWO_PLACES' app/` + `grep -rn 'Decimal("0.01")' app/`. Distinct
files declaring a module-level `TWO_PLACES`/`_TWO_PLACES = Decimal("0.01")`
constant -- 19, exactly the count E-26 states:

| # | file:line | constant name |
| --- | --- | --- |
| 1 | `app/services/tax_calculator.py:29` | `TWO_PLACES` |
| 2 | `app/services/csv_export_service.py:21` | `TWO_PLACES` |
| 3 | `app/services/debt_strategy_service.py:31` | `TWO_PLACES` |
| 4 | `app/routes/investment.py:60` | `TWO_PLACES` |
| 5 | `app/services/calibration_service.py:19` | `TWO_PLACES` |
| 6 | `app/services/savings_goal_service.py:16` | `_TWO_PLACES` |
| 7 | `app/services/budget_variance_service.py:34` | `_TWO_PLACES` |
| 8 | `app/services/paycheck_calculator.py:51` | `TWO_PLACES` |
| 9 | `app/services/escrow_calculator.py:11` | `TWO_PLACES` |
| 10 | `app/services/savings_dashboard_service.py:55` | `_TWO_PLACES` |
| 11 | `app/routes/obligations.py:35` | `TWO_PLACES` |
| 12 | `app/services/dashboard_service.py:34` | `_TWO_PLACES` |
| 13 | `app/services/year_end_summary_service.py:60` | `TWO_PLACES` |
| 14 | `app/services/growth_engine.py:20` | `TWO_PLACES` |
| 15 | `app/services/pension_calculator.py:18` | `TWO_PLACES` |
| 16 | `app/services/investment_projection.py:23` | `TWO_PLACES` |
| 17 | `app/services/retirement_gap_calculator.py:18` | `TWO_PLACES` |
| 18 | `app/services/amortization_engine.py:26` | `TWO_PLACES` |
| 19 | `app/services/spending_trend_service.py:30` | `_TWO_PLACES` |

Plus inline `Decimal("0.01")` quantize targets in files that do NOT even
redeclare the constant (the literal itself reproduced a 20th, 21st ... time):
`app/routes/loan.py:968`, `app/services/interest_projection.py:114`,
`app/services/balance_calculator.py:275`, `app/services/retirement_dashboard_
service.py:197,211,214,240,390` (and `_PCT_QUANTUM = Decimal("0.01")` at
`retirement_dashboard_service.py:76`, a same-literal rate quantum). The
`app/schemas/validation.py:589,602,1895,1921` `Decimal("0.01")` hits are
Marshmallow `Range(min=...)` validators, NOT quantize targets -- excluded
(not a money-rounding site).

### (b) The 24 monetary `.quantize()` sites with NO `rounding=` argument (banker's rounding)

Sweep: `grep -rn 'quantize' app/`, cross-checked against `grep -rn
'ROUND_HALF\|ROUND_CEIL' app/`. The bare-`.quantize()` sites (no `rounding=`
keyword, no positional rounding mode -> Python decimal-context default
`ROUND_HALF_EVEN`). Each line below was Read in this main session and classified
monetary boundary vs intermediate:

| # | file:line | the bare quantize | monetary? | boundary / intermediate |
| --- | --- | --- | --- | --- |
| 1 | `investment_projection.py:93` | `gross = (salary / pay_per_year).quantize(TWO_PLACES)` | yes (biweekly gross $) | **intermediate** (feeds `:96` pct calc; E-26 says intermediates must NOT be quantized at all) |
| 2 | `investment_projection.py:96` | `amt = (gross * amt).quantize(TWO_PLACES)` | yes (contribution $) | boundary (returned `:97`) |
| 3 | `investment_projection.py:159-160` | `periodic_contribution += (total_contrib / num_periods_with_contrib).quantize(TWO_PLACES)` | yes (per-period contribution $) | intermediate (accumulated) |
| 4 | `investment.py:131` | `salary_gross_biweekly = (... / ...).quantize(Decimal("0.01"))` | yes (gross biweekly $) | intermediate (feeds deduction calc) |
| 5 | `investment.py:223` | `chart_balances.append(str(pb.end_balance.quantize(Decimal("0.01"))))` | yes (projected balance $) | boundary (chart display string) |
| 6 | `investment.py:226` | `str((current_balance + cumulative_contrib).quantize(Decimal("0.01")))` | yes (cumulative contribution $) | boundary (chart display string) |
| 7 | `investment.py:319` | `suggested_amount = (remaining_limit / max(remaining_periods, 1)).quantize(TWO_PLACES)` | yes (suggested contribution $) | boundary (form default) |
| 8 | `investment.py:458` | `).quantize(Decimal("0.01"))` (what-if salary_gross_biweekly) | yes ($) | intermediate |
| 9 | `investment.py:535` | `chart_balances.append(str(pb.end_balance.quantize(Decimal("0.01"))))` | yes (projected balance $) | boundary (chart display) |
| 10 | `investment.py:538` | `str((current_balance + cumulative_contrib).quantize(Decimal("0.01")))` | yes ($) | boundary (chart display) |
| 11 | `investment.py:580` | `str(pb.end_balance.quantize(Decimal("0.01")))` | yes (projected balance $) | boundary (chart display) |
| 12 | `investment.py:585` | `committed_end = projection[-1].end_balance.quantize(TWO_PLACES)` | yes (end balance $) | boundary (comparison card) |
| 13 | `investment.py:586-587` | `whatif_end = what_if_projection[-1].end_balance.quantize(TWO_PLACES,)` | yes ($) | boundary |
| 14 | `investment.py:589` | `difference = (whatif_end - committed_end).quantize(TWO_PLACES)` | yes (delta $) | boundary |
| 15 | `investment.py:670` | `).quantize(TWO_PLACES)` | yes ($) | boundary |
| 16 | `savings_dashboard_service.py:266` | `salary_gross_biweekly = (... / ...).quantize(Decimal("0.01"))` | yes (gross biweekly $) | intermediate |
| 17 | `savings_dashboard_service.py:872` | `"total_debt": total_debt.quantize(_TWO_PLACES),` | yes (total debt $) | boundary (returned dict) |
| 18 | `savings_dashboard_service.py:873` | `"total_monthly_payments": total_monthly.quantize(_TWO_PLACES),` | yes (monthly payments $) | boundary (returned dict) |
| 19 | `retirement_dashboard_service.py:197` | `current_gross_biweekly = (... / ...).quantize(Decimal("0.01"))` | yes (gross biweekly $) | intermediate |
| 20 | `retirement_dashboard_service.py:211` | `final_gross_biweekly = (... / ...).quantize(Decimal("0.01"))` | yes ($) | intermediate |
| 21 | `retirement_dashboard_service.py:214` | `gap_net_biweekly = (final_gross_biweekly * effective_take_home_rate).quantize(Decimal("0.01"))` | yes (net biweekly $) | intermediate (fed to `calculate_gap` `:228-232`) |
| 22 | `retirement_dashboard_service.py:240` | `(gap_result.projected_total_savings * swr / 12).quantize(Decimal("0.01"))` | yes (monthly investment income $) | boundary (chart_data string) |
| 23 | `retirement_dashboard_service.py:390` | `salary_gross_biweekly = (... / ...).quantize(Decimal("0.01"))` | yes (gross biweekly $) | intermediate |
| 24 | `loan.py:968` | `committed_interest_saved = (original_interest - committed_interest).quantize(Decimal("0.01"))` | yes (interest saved $) | boundary (rendered in `_payoff_results.html`) |

Count: **24**, reconciling exactly with E-26's stated "24 monetary
`.quantize()` sites" (`00_priors.md:292`); the E-26 read-verified seed sites
(`investment_projection.py:93,96,159` and `retirement_dashboard_service.py:197,
211,214`, `:294`) all re-resolve at live source this session and are rows
1-3 / 19-21 above.

### (c) Non-monetary bare-quantize sites -- classified and EXCLUDED (not E-26 findings)

- `retirement_dashboard_service.py:307-308` `(settings.safe_withdrawal_rate *
  _PCT_SCALE).quantize(_PCT_QUANTUM,)` and `:328` `(weighted_return /
  total_balance * _PCT_SCALE).quantize(_PCT_QUANTUM)` -- Read this session: a
  **percentage** for the dashboard SWR/return slider (`_PCT_QUANTUM`, not a
  money value). Not a monetary boundary; excluded from the 24.
- `loan.py:182` `raw_pct.quantize(one_decimal, rounding=ROUND_DOWN)` -- a
  display **percentage truncation**, has an explicit rounding mode; not money,
  not banker's; excluded.

### (d) The sanctioned ROUND_CEILING exception -- explicitly EXCLUDED per E-26

`savings_goal_service.py:462-463`, `_compute_required_monthly`, Read in full
this session (`:431-464`):

```
return (remaining / Decimal(str(months_available))).quantize(
    _TWO_PLACES, rounding=ROUND_CEILING
)
```

Docstring `:438` "Uses ROUND_CEILING so the user contributes at least enough."
This is precisely the E-26 sanctioned exception (`00_priors.md:290`: "The sole
current sanctioned exception is `savings_goal_service._compute_required_monthly`
(`savings_goal_service.py:462-463`) ... it is a documented exception, NOT a
finding"). **Explicitly excluded as a finding** (contract item 6;
`phase6_plan.md` G7). The two other `ROUND_CEILING` hits in the sweep
-- `savings_goal_service.py:393` and `loan.py:1143` -- are
`to_integral_value(rounding=ROUND_CEILING)` on an **integer month count**
(ceiling division), not a money `.quantize()`; not E-26 sites, not findings.

### (e) Intermediate-quantization sub-defect (the other half of the E-26 root)

E-26 (`:286-288`) also requires full-precision intermediates: intermediate
results are NOT quantized; quantize only at the storage/display boundary. The
remaining ~99 quantize sites in the sweep (e.g. the `amortization_engine.py`
11-site cluster, `paycheck_calculator.py` 7, `tax_calculator.py` 7) pass an
explicit `ROUND_HALF_UP` so they are NOT banker's-rounding findings, but several
quantize an intermediate against the full-precision clause. Two Read at source
this session as representative proof:

- `balance_calculator.py:274-276` `interest_portion = (running_principal *
  monthly_rate).quantize(Decimal("0.01"), ROUND_HALF_UP)` -- an **intermediate**
  (`principal_portion = total_payment_in - interest_portion` `:277`) quantized
  mid-loop, then re-used; correct rounding mode but a quantized intermediate.
- `investment_projection.py:93` (row 1 above) -- a quantized intermediate AND
  banker's.

These are recorded as the structural intermediate-rounding facet of the same
root, not separately ID'd (E-26 governs both facets under one helper).

**Expanded comparison (DRY).** The single rule E-26 mandates, versus its 19+
hand-redeclared forms. Canonical (does not exist -- E-26's `app/utils/money.py`
`round_money`):

```
# E-26 intended single source (NOT PRESENT in app/)
def round_money(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
```

Reproduced form, file after file (`tax_calculator.py:29` + `:209`):

```
TWO_PLACES = Decimal("0.01")
...
return total_tax.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)
```

(`amortization_engine.py:26` + `:197`):

```
TWO_PLACES = Decimal("0.01")
...
return payment.quantize(TWO_PLACES, ROUND_HALF_UP)
```

versus the drifted bare form (`investment_projection.py:23` + `:96`):

```
TWO_PLACES = Decimal("0.01")
...
amt = (gross * amt).quantize(TWO_PLACES)        # NO rounding -> ROUND_HALF_EVEN
```

The constant declaration and the quantize call are the same calculation
(2dp money rounding) written 19+ times; in 24 of those writings the
`rounding=` half of the calculation was silently dropped, so the same
conceptual operation produces `ROUND_HALF_UP` cents in 99 places and
`ROUND_HALF_EVEN` cents in 24 -- the textbook drift E-26 describes.

**Governing E-NN.** E-26 (`00_priors.md:286-296`). E-26 already locks the
single source; this finding documents the 19-file + 24-site live divergence
from it and stops (contract item 6).

**Recommended single source of truth (report only).** One `app/utils/money.py`
exposing `round_money(x)` (2dp, ROUND_HALF_UP, the only default) and explicitly
named sanctioned variants (`round_money_ceiling(x)` for the
`savings_goal_service` case, re-expressed through the named variant); no
`rounding=` kwarg anywhere; every one of the 19 `TWO_PLACES` declarations and
every boundary `.quantize()` routed through it; intermediates left at full
precision (drop the `balance_calculator.py:274` / `investment_projection.py:93`
class of mid-calc quantize). This is exactly E-26's stated remediation
(`00_priors.md:296`), restated report-only; no diff produced.

**Inherited-vs-independent.** Carried from `phase6_plan.md` section 1
(Money-rounding helper row: "19-file `TWO_PLACES` constant redeclaration; 24
monetary `.quantize()` sites silently using banker's rounding") and from the
E-26 Phase-6 verification direction (`00_priors.md:296`: "the absence of a
centralized helper plus the 19-file constant redeclaration as the Phase-6 DRY
root cause"). **Confirmed at source, both counts exact:** 19 declaring files
(register (a)), 24 monetary bare-quantize sites (register (b)). No narrowing or
contradiction; the carried characterization holds precisely.

**Blast radius.** Any displayed dollar figure produced through one of the 24
bare sites differs by up to one cent from the `ROUND_HALF_UP` convention every
hand-computed test assertion assumes -- concretely the investment/retirement
projection chart balances (`investment.py:223,226,535,538,580`), the savings
debt summary `total_debt`/`total_monthly_payments`
(`savings_dashboard_service.py:872-873`), and the retirement monthly
investment-income figure (`retirement_dashboard_service.py:240`). Recorded by
Phase 3/5 as DIVERGE against E-26 (`00_priors.md:296`: "Phase 3 and Phase 5
record the 24 banker's-rounding sites as DIVERGE against E-26"); this is the
structural root under that observed divergence.

---

## D6-08 -- The `effective_amount` selection rule is hand-rolled as a 2-tier mirror in 5+ sites instead of using the canonical property

**Principle.** DRY (`financial_calculation_audit_plan.md:638-639` verbatim:
"Effective-amount selection (`actual if actual is not None else estimated`)
reproduced inline in multiple files."). Canonical single source is the
`Transaction.effective_amount` 4-tier property whose own docstring
(`transaction.py:231`, cited `03_consistency.md:2146`) calls itself "the single
source of truth ... for what amount a transaction contributes."

**Grep / sites (this session, Explore sweep `grep -rn 'actual_amount'` /
`'estimated_amount'` / `'effective_amount'` across `app/services/`,
`app/routes/`, `app/models/`, `app/utils/`, `app/templates/`,
`app/static/js/`).**

Canonical producers (Read at source this session via the P3-c substrate,
`03_consistency.md:2144-2163`): `Transaction.effective_amount`
@`app/models/transaction.py:221-245` (4-tier: is_deleted -> 0;
excludes_from_balance -> 0; `actual_amount if actual_amount is not None`; else
`estimated_amount`); `Transfer.effective_amount`@`app/models/transfer.py:174-182`
(2-tier).

Hand-rolled 2-tier mirror sites (`actual_amount if ... is not None/none else
estimated_amount`), each Read at source this session:

| # | file:line | form | layer | tier-1/2 guard (from P3-c, re-confirmed) |
| --- | --- | --- | --- | --- |
| 1 | `credit_workflow.py:229` | `txn.actual_amount if txn.actual_amount is not None else txn.estimated_amount` | service (Python ternary) | P3-c S14: tier-2 hard-guarded `credit_workflow.py:192-196`; value seeds a payback `estimated_amount` `:241` |
| 2 | `budget_variance_service.py:390-393` | `if txn.actual_amount is not None: return txn.actual_amount` / `return txn.estimated_amount` (if-return form) | service (Python branch) | P3-c S10: query filters `is_deleted` `:253,287` (=tier1) + `~status_id.in_([CREDIT,CANCELLED])` `:254,288` (=tier2) |
| 3 | `grid/_transaction_cell.html:17` | `{% set display_amount = t.actual_amount if t.actual_amount is not none else t.estimated_amount %}` | template (Jinja) | P3-c T1: grid query `is_deleted=False` (`grid.py:222`); display face-amount + status badge |
| 4 | `grid/_mobile_grid.html:92` | `{% set display_amount = txn.actual_amount if txn.actual_amount is not none else txn.estimated_amount %}` | template (Jinja) | P3-c T4: same as T1, mobile |
| 5 | `grid/_mobile_grid.html:179` | `{% set display_amount = txn.actual_amount if txn.actual_amount is not none else txn.estimated_amount %}` | template (Jinja) | P3-c T4: same, second mobile section (mirror duplicated x2 within the one file) |
| 6 | `grid/_transaction_full_edit.html:39` | `value="{{ txn.actual_amount if txn.actual_amount is not none else '' }}"` | template (Jinja, form binding) | **newly found this phase** -- degenerate mirror: tier-3 selector with `''` else (form input prefill, not a balance read) |

**Expanded comparison (DRY).** Canonical tier-3/tier-4 (`transaction.py:245`,
Read this session via `03_consistency.md:2151-2153`):

```
return self.actual_amount if self.actual_amount is not None else self.estimated_amount
```

Site 1 (`credit_workflow.py:229`):

```
payback_amount = txn.actual_amount if txn.actual_amount is not None else txn.estimated_amount
```

Site 3 (`_transaction_cell.html:17`):

```
{% set display_amount = t.actual_amount if t.actual_amount is not none else t.estimated_amount %}
```

Sites 4/5 (`_mobile_grid.html:92` and `:179`) -- byte-identical to site 3 with
`txn` for `t`. Site 2 (`budget_variance_service.py:390-393`) is the same
selection unrolled into `if/return`. Each reproduces tiers 3-4 of the property
verbatim; none re-checks tiers 1-2 in the expression itself (they rely on an
upstream query filter or a status precondition, per P3-c's per-site guard
column). Site 6 reproduces only the tier-3 "actual if not None" selector with a
display-empty else, a structural cousin (the actual-vs-null branch shape is the
duplicated thing).

**Governing E-NN.** E-25 family (the `effective_amount` accessor is the
period-subtotal/balance base, `00_priors.md` E-25 / `phase6_plan.md` section 1
"effective_amount mirror (E-25 family)") and the E-10/E-15 standards family for
the `is not None` (NOT truthiness) tier-3 distinction (`00_priors.md:362` E-12,
`:374` E-15). One canonical accessor already exists (`Transaction.effective_
amount`); no new target is invented.

**Recommended single source of truth (report only).** Every consumer that needs
"the amount this transaction contributes" calls `txn.effective_amount` (or, for
display contexts that intentionally show face amount + status badge, a single
shared `display_amount` template global / context processor computed once in the
route), rather than re-deriving tiers 3-4 inline. The degenerate form-binding
mirror (site 6) is a form-prefill concern, not a balance concern, and is noted
structural-only. Report only; no diff.

**Inherited-vs-independent.** Sites 1-5 carried from `phase6_plan.md` section 1
("4 hand-rolled 2-tier `actual if not None else estimated` mirrors
(S10/S14/T1/T4)") and the P3-c sweep's explicit Phase-6 DRY tags
(`03_consistency.md:2227` T1 "Phase-6 DRY (hand-rolled 2-tier mirror)", `:2230`
T4 "Phase-6 DRY (mirror duplicated x2 within file)", `:2266-2267` "Phase-6 DRY
notes (the 4 hand-rolled 2-tier mirrors S10/S14/T1/T4"). **Confirmed at source.**
**Sharpened:** the section-1 register says "4 ... mirrors"; the live count is
5 distinct sites (T4 is two physical sites `_mobile_grid.html:92` AND `:179`,
which P3-c collapsed to one table row -- `03_consistency.md:2230` notes
"duplicated x2 within file"). Site 6 (`_transaction_full_edit.html:39`) is
**newly found this phase** and was legitimately out of P3-c's scope: P3-c swept
balance-bypass reads, and the full-edit form field is a write-form prefill, not
a balance contribution; recorded here as a structural mirror only, with the
scope reason noted (not a contradiction of the prior tag).

**Blast radius.** No NEW silent balance drift on this surface: P3-c verdicted
all of S10/S14/T1/T4 EQUIVALENT because each is tier-1/2-guarded by an upstream
filter Read at source (`03_consistency.md:2242-2244,2263-2264`). Latent
structural risk: if the property's tier rule changes (e.g. a 5th tier) and any
of these 5+ inline copies is not updated in lockstep, the copy silently diverges
-- the same duplication-as-drift-substrate thesis (`financial_calculation_audit_
plan.md:618`); cross-linked to F-027 (the consolidated effective_amount sweep,
`03_consistency.md:2175-2267`).

---

## D6-09 -- The status predicate is expressed inline (`status_id != projected_id`, `== cancelled_id`, the `[CREDIT,CANCELLED]` exclusion set) across many files instead of one centralized predicate

**Principle.** DRY (`financial_calculation_audit_plan.md:636-637` verbatim:
"Status filters expressed inline as `txn.status_id != projected_id` in many
places, rather than centralized.") and E-15 (`00_priors.md:374`: reference-table
conditionals compare integer IDs, never string `name` -- satisfied here, but the
ID comparison is reproduced rather than centralized, which is the DRY half).

**Grep / sites (this session, Explore sweep `grep -rn 'status_id != ' / '== ' /
'.in_' / 'excludes_from_balance' / 'is_settled' app/`; full register returned,
the load-bearing reproductions enumerated below).**

(i) The `!= projected_id` skip (the exact pattern the audit plan names),
reproduced inline as a per-loop guard:

| file:line | line | binds projected_id at |
| --- | --- | --- |
| `balance_calculator.py:365` | `if txn.status_id != projected_id:` (entry-formula gate) | `:364` `ref_cache.status_id(StatusEnum.PROJECTED)` |
| `balance_calculator.py:411` | `if txn.status_id != projected_id:` (`_sum_remaining`) | `:406` ref_cache |
| `balance_calculator.py:443` | `if txn.status_id != projected_id:` (`_sum_all`) | `:439` ref_cache |
| `grid.py:269` | `if txn.is_deleted or txn.status_id != projected_id:` (grid subtotal) | grid.py ref_cache |
| `credit_workflow.py:192` | `if txn.status_id != projected_id:` (credit guard) | ref_cache |

(ii) The `== projected_id` query filter, reproduced as a SQLAlchemy predicate:
`dashboard_service.py:145`, `entry_service.py:524`, `transfers.py:494,549,639`,
`templates.py:478,522,593`, `carry_forward_service.py:263,410,428` (11 sites,
each binding `projected_id = ref_cache.status_id(StatusEnum.PROJECTED)` locally).

(iii) The `[CREDIT, CANCELLED]` exclusion set (= `Status.excludes_from_balance`
= P3-c canonical tier-2, `03_consistency.md:2155-2157`), independently
re-centralized **twice** plus reproduced inline:
- `budget_variance_service.py:207-210` builds `excluded_status_ids =
  [ref_cache.status_id(CREDIT), ref_cache.status_id(CANCELLED)]`, used `:254,288`.
- `year_end_summary_service.py:2028-2033` has its OWN `_get_excluded_status_ids()`
  helper returning the same `[CREDIT, CANCELLED]`, used `:665`.
- `loan_payment_service.py:210`, `savings_dashboard_service.py:113`,
  `investment_projection.py:150,186,268` reproduce the same exclusion as
  `Status.excludes_from_balance.is_(False)` / `not t.status.excludes_from_balance`.

(iv) Templates hardcode the same predicate against Jinja constants
(`STATUS_PROJECTED` / `STATUS_CANCELLED` / `STATUS_CREDIT`): `_transaction_
cell.html:19,61`, `_transaction_full_edit.html:89,99,108,118,128`, `grid.html:162,
235`, `_mobile_grid.html:70,94,109,157,181,196`, `_transaction_card.html:56,79`,
`_transfer_full_edit.html:92` -- the grid.html `!= STATUS_CANCELLED` row-match is
duplicated byte-for-byte into `_mobile_grid.html` (sweep note "DUPLICATED from
grid.html", `:70` and `:157`).

**Expanded comparison (DRY).** The single conceptual predicate "is this row a
live, balance-contributing Projected item" is written three structurally
different ways for the same intent:

```
balance_calculator.py:411   if txn.status_id != projected_id: continue   # Python in-loop skip
dashboard_service.py:145     Transaction.status_id == projected_id,        # SQLAlchemy filter
_transaction_cell.html:19    t.status_id == STATUS_PROJECTED               # Jinja constant
```

and the tier-2 exclusion set the same way twice:

```
budget_variance_service.py:207-210   excluded_status_ids = [ref_cache.status_id(CREDIT), ref_cache.status_id(CANCELLED)]
year_end_summary_service.py:2028-2033 def _get_excluded_status_ids(): return [ref_cache.status_id(CREDIT), ref_cache.status_id(CANCELLED)]
```

Two helpers, different names (`excluded_status_ids` local vs
`_get_excluded_status_ids()`), identical bodies and identical intent -- the
exact "two helpers with different names can have identical bodies" hazard
(`financial_calculation_audit_plan.md:441-444`).

**Governing E-NN.** E-15 family (`00_priors.md:374`; ID-based ref logic) -- the
ID comparison itself is compliant; `NONE -> structural-only` for the
centralization of the predicate (no E-NN names a single status-predicate
target). Pure-structure DRY finding, not a correctness one (every site Read this
session computes the same membership; no string-name comparison was found, so
E-15 is satisfied -- the defect is the reproduction, not the mechanism).

**Recommended single source of truth (report only).** One status-predicate
module (e.g. `is_projected(txn)`, `excludes_from_balance_ids()`,
`settled_ids()`) returning the ref-cached ID sets/booleans once, called by every
service, route query, and (via a context processor / Jinja global) every
template, replacing the per-file `ref_cache.status_id(...)` rebind and the two
independent `[CREDIT,CANCELLED]` centralizations. Report only; no diff.

**Inherited-vs-independent.** Carried from `phase6_plan.md` section 1
(Status-filter inline row, "Inline status comparisons reproduced across many
files", audit-plan 6.1 `:637-639`). **Confirmed at source and extended:** the
section-1 register cites only the `status_id != projected_id` shape; this phase
additionally found (not previously tagged) the **double independent
centralization** of the `[CREDIT,CANCELLED]` set
(`budget_variance_service.py:207-210` vs `year_end_summary_service.py:2028-2033`)
and the grid.html -> _mobile_grid.html byte-duplicated `!= STATUS_CANCELLED`
row-match -- recorded as Phase-6 additions to the carried characterization
(duplications Phase 3 did not tag, per contract item 4).

**Blast radius.** The `!= projected_id` reproductions in
`balance_calculator.py:365,411,443` and `grid.py:269` are the same predicate
that governs which rows enter the period subtotal vs the balance row -- the
exact surface of D6-03 / F-002 / F-004 (`02_concepts.md:294,330`): if any one
of these inline copies is edited out of step (e.g. a new status added to the
include set in the balance calculator but not the grid loop), the grid subtotal
silently diverges from the balance row with no test catching it. Cross-linked to
D6-03 and F-002/F-004.

---

## D6-10 -- The 4% SWR / 7% assumed-return fallback is a magic literal duplicated across two unit conventions instead of one named constant

**Principle.** "No magic numbers or strings" (`docs/coding-standards.md` Python
"Code Structure": "Every numeric ... literal representing a business rule must
be a named constant ... Do not write `Decimal("0.062")` when you mean
`SOCIAL_SECURITY_RATE`") and DRY, governed by PA-05 (`00_priors.md:814`:
"Hardcoded fallback percentages `0.04`, `4.0`, `7.0` for safe withdrawal rate
and assumed return are magic numbers without named constants or source
citations.").

**Grep / sites (this session, Explore sweep `grep -rn '0\.04\|4\.0\|7\.0\|0\.062
\|0\.0145' app/` scoped to retirement/growth + `safe_withdrawal|assumed_return|
growth_rate` symbols; every hit Read at source this session).**

Named constants that DO exist (Read this session, `retirement_dashboard_
service.py:47-76`):
- `_DEFAULT_SWR_PCT = Decimal("4.00")` `:54`, with a Trinity-Study (Cooley,
  Hubbard, Walz 1998) source citation in the docblock.
- `_DEFAULT_RETURN_PCT = Decimal("7.00")` `:63`, with a Damodaran historical-
  returns source citation.
- Note the convention: these are **percent** (`4.00`, `7.00`); the DB columns
  and the fallbacks are **fraction** (`0.0400`, `0.07000`). Same business rule,
  two representations.

Magic-literal fallbacks reproducing the same 4% SWR rule WITHOUT the constant
(Read this session):
| file:line | literal | context | named constant used? |
| --- | --- | --- | --- |
| `retirement_dashboard_service.py:220` | `Decimal(str(settings.safe_withdrawal_rate or "0.04")) if settings else Decimal("0.04")` (the literal `0.04` appears **twice** on this one line) | `compute_gap_data` SWR fallback | NO -- `_DEFAULT_SWR_PCT` is defined `:54` in the SAME file, unused here |
| `retirement_gap_calculator.py:41` | `safe_withdrawal_rate=Decimal("0.04")` | `calculate_gap` parameter default | NO (docstring says "4% rule" but no constant) |
| `app/models/user.py:238` | `default=0.0400, server_default=db.text("0.0400")` | `safe_withdrawal_rate` column default | NO (CHECK-constraint comment references "4% rule" / F-077 / C-24) |
| `app/models/investment_params.py:81` | `default=0.07000, server_default=db.text("0.07000")` | `assumed_annual_return` column default | NO -- mirrors `_DEFAULT_RETURN_PCT` value, no reference |

**Expanded comparison (DRY).** The same 4%-safe-withdrawal business rule, four
ways, two unit conventions:

```
retirement_dashboard_service.py:54   _DEFAULT_SWR_PCT = Decimal("4.00")        # percent, cited
retirement_dashboard_service.py:220  ... or "0.04")) if settings else Decimal("0.04")   # fraction, uncited, x2 on the line
retirement_gap_calculator.py:41      safe_withdrawal_rate=Decimal("0.04")      # fraction, param default
user.py:238                          default=0.0400, server_default="0.0400"   # fraction, column default
```

`Decimal("4.00")` and `Decimal("0.04")` are the same rule (`_PCT_SCALE =
Decimal("100")` at `retirement_dashboard_service.py:~70` is the documented
bridge); they are maintained as four independent literals across two
conventions, so a change to the default SWR (or a correction to the citation)
must be made in four places that no test cross-checks -- the magic-number drift
PA-05 names.

**Governing E-NN.** PA-05 (`00_priors.md:814`) -- the standards-derived
expectation (named constants + source citations for fallback rates). No
correctness E-NN governs the numeric value itself; this is the
named-constant/DRY structural finding PA-05 already opened.

**Recommended single source of truth (report only).** One constants location
(or the existing `_DEFAULT_SWR_PCT` / `_DEFAULT_RETURN_PCT` promoted to a shared
module) holding the rate once with its citation, plus one documented
fraction<->percent converter; `retirement_dashboard_service.py:220`,
`retirement_gap_calculator.py:41`, and the `user.py`/`investment_params.py`
column defaults all reference it instead of re-spelling `0.04`/`0.0400`/
`0.07000`. Report only; no diff.

**Inherited-vs-independent.** Carried from `phase6_plan.md` section 1
(Magic-number fallbacks row, "`0.04`, `4.0`, `7.0` SWR / assumed-return
fallbacks", `00_priors.md:814` PA-05). **Confirmed at source and sharpened:**
PA-05 phrases it as "without named constants"; the live state is subtler and
worse-for-drift -- the named constants `_DEFAULT_SWR_PCT`/`_DEFAULT_RETURN_PCT`
**do** exist (with good citations) but in a different unit convention and are
**not used** by the `:220` fallback in the very same file, so the codebase
carries both the named constant and the uncited magic literal for the same
rule. Recorded as a Phase-6 sharpening of PA-05 (the issue is unused-constant +
dual-convention duplication, not pure absence). The A-26 `estimated_retirement_
tax_rate` NULL-semantics tail (`retirement_dashboard_service.py:222-226`,
adjacent to `:220`) is out of structural scope and carried forward unchanged,
not resolved here.

**Blast radius.** The retirement gap analysis `required_retirement_savings`
(via `calculate_gap` `safe_withdrawal_rate`, `retirement_gap_calculator.py:92-95`)
and the dashboard SWR slider default both consume this rate; if the `:220`
fallback literal and `_DEFAULT_SWR_PCT` ever disagree (one corrected, the other
missed) the retirement gap figure and the slider default silently disagree for
any user with no `UserSettings` row. No prior numeric divergence observed (PA-05
is `open`, not yet a Phase 3/5 DIVERGE); latent structural drift only.

---

### Part 6.1 (cross-cutting micro-duplications) -- session P6-a2 complete

Four `D6-` roots recorded (D6-07 rounding-helper absence: full 19-file
`TWO_PLACES` register + classified 24-site monetary banker's-rounding register,
the `ROUND_CEILING` `savings_goal_service.py:462-463` exception explicitly
excluded per E-26/contract item 6, the non-monetary `_PCT_QUANTUM`/`ROUND_DOWN`
sites excluded with reason; D6-08 effective_amount 5+1-site mirror inventory;
D6-09 inline status-filter inventory incl. the double `[CREDIT,CANCELLED]`
re-centralization; D6-10 PA-05 4%/7% magic-literal inventory). Every finding
carries every `phase6_plan.md` section-3 element; every "N places" claim is
backed by N pasted `file:line` citations swept this session via Explore and the
classification lines Read in this main session. Both E-26 carried counts
re-resolved exactly (19 files, 24 monetary sites). New-this-phase additions
beyond the section-1 register recorded as such (D6-08 site 6
`_transaction_full_edit.html:39`; D6-09 double-centralized exclusion set + grid
-> mobile byte-duplicate; D6-10 unused-constant/dual-convention sharpening of
PA-05). No fix written; no source/test/migration/template/JS touched. SOLID
(Part 6.2, P6-b) and Boundary (Part 6.3, P6-c) are out of this session's scope.

# Part 6.2 SOLID -- service and route design (session P6-b)

Audit-plan reference: `financial_calculation_audit_plan.md:644-669` (6.2, all
five principles). Contract: SRP/OCP proven by the live file's current line
count and current branch structure, never trusted from the roadmap
(`phase6_plan.md` section 2 item 3; `financial_calculation_audit_plan.md:650-657`).

Method, this session: `wc -l app/services/*.py app/routes/*.py` plus an AST
pass (`ast.FunctionDef.end_lineno - lineno + 1`) over every file in
`app/services/` and `app/routes/` to get exact per-function line spans; every
function over 200 lines Read in full this session; the OCP type-dispatch sweep
run through the Explore subagent (contract item 8) and the returned register
folded here with the classification lines Read in this main session.

The complete >=150-line function inventory (AST, this session):

```
 295  app/routes/investment.py:66-360  dashboard
 294  app/services/amortization_engine.py:326-619  generate_schedule
 241  app/routes/investment.py:366-606  growth_chart
 195  app/routes/grid.py:167-361  index
 189  app/services/carry_forward_service.py:291-479  carry_forward_unpaid
 182  app/routes/transactions.py:307-488  update_transaction
 176  app/services/retirement_dashboard_service.py:79-254  compute_gap_data
 171  app/services/transfer_service.py:443-613  update_transfer
 168  app/routes/loan.py:408-575  dashboard
 163  app/services/retirement_dashboard_service.py:338-500  _project_retirement_accounts
 162  app/routes/obligations.py:262-423  summary
 162  app/routes/loan.py:863-1024  payoff_calculate
 162  app/routes/auth.py:738-899  mfa_verify
 161  app/services/transfer_service.py:688-848  restore_transfer
 161  app/routes/templates.py:293-453  update_template
 158  app/services/transfer_service.py:283-440  create_transfer
 158  app/routes/transfers.py:306-463  update_transfer_template
 156  app/services/paycheck_calculator.py:92-247  calculate_paycheck
 153  app/services/carry_forward_service.py:602-754  _resolve_envelope_target_fields
```

Only three functions exceed the audit-plan's 200-line SRP threshold
(`financial_calculation_audit_plan.md:651-652`; `00_priors.md:129`): two route
handlers in `investment.py` and one pure-computation service function in
`amortization_engine.py`. None of the roadmap's / contract's named SRP
candidates (`savings.py:dashboard`, `year_end_summary_service.py`,
`carry_forward_service.py`, `savings_dashboard_service.py`,
`dashboard_service.py`) currently contains a >200-line function -- the
single largest function in any of them is
`year_end_summary_service.py:_build_investment_balance_map` at 145 lines and
`carry_forward_service.py:carry_forward_unpaid` at 189. That divergence is
itself recorded under S6-01.

## S6-01 -- SRP: the 470-line `savings.py:dashboard` monolith was extracted to a service, but the identical concern-mix persists un-extracted in `investment.py:dashboard` (295 LOC) and `investment.py:growth_chart` (241 LOC)

- **Principle.** Single Responsibility -- "Does the file do one thing, or does
  it mix HTTP, business logic, and data access? ... List every service or route
  function over 200 lines that mixes concerns"
  (`financial_calculation_audit_plan.md:648-652`); coding-standards 50/100-line
  decomposition thresholds (`docs/coding-standards.md:30-32`;
  `00_priors.md:129`).

- **Roadmap claim under test.** The audit plan restates the roadmap's
  "470-line `savings.py:dashboard` SRP violation" and orders it re-verified by
  grep (`financial_calculation_audit_plan.md:650-652`;
  `phase6_plan.md` section 2 item 3).

- **Live metric / verdict on the roadmap claim.** `wc -l app/routes/savings.py`
  = **288 lines** (whole file). `savings.py:dashboard` is now
  `app/routes/savings.py:107-113` -- a **4-line thin delegator**:

  ```python
  107  @savings_bp.route("/savings")
  ...
  110  def dashboard():
  111      """Savings dashboard: account balances, goals, and emergency fund metrics."""
  112      ctx = savings_dashboard_service.compute_dashboard_data(current_user.id)
  113      return render_template("savings/dashboard.html", **ctx)
  ```

  The 470-line `savings.py:dashboard` SRP claim **does NOT hold at current
  source**: the body was extracted to
  `savings_dashboard_service.py:compute_dashboard_data` (line 61). The roadmap's
  "addressed" is, for this one site, *true*.

- **But the SRP root was relocated, not eliminated.** The same monolith pattern
  (one route function owning HTTP + many inline ORM queries + business
  projection logic) persists, un-extracted, in `investment.py`. There is a
  `savings_dashboard_service` but **no equivalent investment-dashboard
  service**. Live metrics, Read in full this session:

  - `app/routes/investment.py:66-360` `dashboard` -- **295 lines**. Concern
    mix in one body: HTTP (`get_or_404` :68, `abort` :70, `render_template`
    :340-360); **8 distinct inline ORM queries** -- `InvestmentParams`
    (:72-76), `Transaction` (:94-103), `SalaryProfile` (:122-126),
    `PaycheckDeduction` (:134-144), `Transaction` again (:160-171),
    `TransferTemplate` (:273-282), `Account` (:324-333), `UserSettings`
    (:249-253); business logic -- balance calc (:106-118), projection
    (:209-227), limit/horizon math (:230-260), suggested-contribution math
    (:305-322).
  - `app/routes/investment.py:366-606` `growth_chart` -- **241 lines**, same
    mix: HTTP (HX-Request guard :383, `render_template` :598), inline ORM
    queries `Account` (:386), `InvestmentParams` (:390-394), `SalaryProfile`
    (:449-453), `PaycheckDeduction` (:460-470), `Transaction` (:415-424,
    :484-495); business -- balance calc, projection, what-if overlay
    (:541-596).

- **Sites.** `app/routes/investment.py:66-360`, `:366-606` (both Read in full
  this session); `app/routes/savings.py:107-113` and
  `app/services/savings_dashboard_service.py:61` (the contrast control, Read).

- **Governing E-NN.** `NONE -> structural-only`. No E-18..E-28 governs route
  layering; this is a pure SRP structure finding (severity assigned in Phase 8,
  not here).

- **Recommended single source of truth (report only).** Extract the
  `investment.py:dashboard` / `growth_chart` data-assembly + projection bodies
  into an `investment_dashboard_service` mirroring the already-correct
  `savings_dashboard_service.compute_dashboard_data` shape (route becomes a
  thin delegator returning a context dict). The lines `investment.py:444-523`
  are a near-verbatim re-implementation of `investment.py:120-218`
  (salary_gross_biweekly -> deductions -> adapted_deductions ->
  acct_contributions -> `calculate_investment_inputs` ->
  `build_contribution_timeline` -> `project_balance`); the single service
  method removes that route-to-route duplication as a by-product.

- **Inherited-vs-independent note.** Contradicts the carried Phase-3/roadmap
  `savings.py:dashboard` 470-line tag (`financial_calculation_audit_plan.md:650-652`):
  the tagged site is now 4 lines (claim stale / no longer resolves). The live
  SRP violation at `investment.py:dashboard`/`growth_chart` is **new this
  phase** -- not in the `phase6_plan.md` section-1 register, not a Phase-3
  `Phase-6` tag. Both citations recorded per contract item 4.

- **Blast radius.** No numeric drift observed yet (the extracted-vs-inline
  split does not by itself change a figure); latent structural. The
  `investment.py:444-523` ~vs~ `:120-218` duplication is the latent risk: a
  contribution-timeline fix applied to one route and not the other would drift
  the growth-chart balance from the dashboard balance for the same account
  (no Phase 3/4/5 numeric finding has exercised this yet -- "no observed drift
  yet").

## S6-02 -- SRP (negative finding): `amortization_engine.generate_schedule` is 294 lines but is a single cohesive pure algorithm with NO concern-mix -- a length/justification finding only, NOT an audit-plan SRP violation

- **Principle.** Single Responsibility, the audit-plan concern-mix test
  (`financial_calculation_audit_plan.md:648-649`); the coding-standards
  ">100 lines requires justification" rule (`docs/coding-standards.md:30-32`).

- **Live metric.** `app/services/amortization_engine.py:326-619` --
  **294 lines** (Read in full this session). It exceeds the 200-line bar, so
  the audit plan requires it be listed and classified.

- **Verdict.** Classified as **NOT a concern-mix SRP violation**. The body
  contains zero HTTP and zero data access -- it imports no Flask, issues no
  `db.session` query; its inputs are primitives plus the
  `PaymentRecord`/`RateChangeRecord` frozen dataclasses (:335-336) and its
  output is a `list[AmortizationRow]`. It does exactly one thing: produce a
  month-by-month amortization schedule. The length is driven by four
  legitimately-coupled sub-cases of the *same* algorithm (contractual-vs-
  re-amortized payment :430-455; anchor reset :486-493; ARM rate adjustment
  :498-514; payment-record-vs-contractual P&I split :523-591), each documented
  in the 32-line docstring (:340-403) -- the docstring is the
  ">100 lines requires justification" justification the standard asks for.
  Recorded explicitly so it is NOT miscounted as an SRP concern-mix alongside
  S6-01.

- **Governing E-NN.** `NONE -> structural-only` (and below the bar for a
  structure finding -- recorded as a classified non-finding so the >200-line
  inventory is complete and the reader sees it was examined, not skipped).

- **Inherited-vs-independent note.** New this phase; not in the section-1
  register. Not contradicting any tag.

- **Blast radius.** None. (Behaviorally this is the canonical schedule
  producer; its *consumers* re-assembling `(P,r,n)` is the D6-01 DRY finding,
  not an SRP fault of this function.)

## S6-03 -- SRP + OCP + LSP: two independent per-account-type calculator dispatchers (plus a third partial copy), with divergent branch order and dispatch key

- **Principle.** SRP (two modules each own "select the projection engine for an
  account type", `financial_calculation_audit_plan.md:648-649`); OCP ("Does the
  file branch on AccountType ... rather than on metadata flags",
  `:653-657`); LSP ("calculation services that handle multiple account types
  ... or do they branch on subtype in ways that would break if a new type were
  added", `:658-661`).

- **Sites (all Read in full this session).**
  - Dispatcher A: `app/services/savings_dashboard_service.py:294-438`
    `_compute_account_projections` (**145 lines**). Branch ladder by
    param-map presence: interest (`acct_interest_params` :334) ->
    `calculate_balances_with_interest` :335; loan (`acct_loan_params` :355) ->
    `get_loan_projection` :362 + schedule walk :378-387; investment
    (`acct_investment_params` :388) -> `_project_investment` :389;
    `else` :393-400 -> plain `calculate_balances` (via :343).
  - Dispatcher B: `app/services/year_end_summary_service.py:2036-2128`
    `_get_account_balance_map` (**93 lines**). Branch ladder by metadata flag:
    `acct_type.has_amortization` + `debt_schedules` :2071 ->
    `_schedule_to_period_balance_map` :2079; `acct_type.has_interest` +
    `interest_params` :2105 -> `calculate_balances_with_interest` :2108;
    `has_parameters and not has_interest and not has_amortization` :2114-2118
    -> `_build_investment_balance_map` :2121; `else` :2127 -> plain
    `calculate_balances`.
  - Third partial copy: the `needs_setup` ladder inside Dispatcher A,
    `savings_dashboard_service.py:402-409`, re-expresses the *same*
    `has_parameters / has_interest / has_amortization` taxonomy a third time.

- **Expanded comparison (the divergence that makes this a finding, not one
  dispatcher called twice).** A and B answer the identical question -- "which
  calculator produces this account's period balances?" -- but disagree on:
  (1) **dispatch key**: A keys on the presence of a pre-loaded param-map entry
  (`params["loan_params_map"].get(acct.id)`), B keys on the
  `account_type.has_amortization`/`has_interest` boolean column;
  (2) **branch order**: A tests interest before loan; B tests amortization
  before interest; (3) **loan path**: A calls the live
  `get_loan_projection` + walks `proj.schedule` (:378-387), B consumes a
  pre-generated `debt_schedules` map via `_schedule_to_period_balance_map`
  (:2079). Same account, two code paths to "the loan's projected balance."

- **OCP facet / live branch construct.** Adding one new parameterized account
  type requires editing **three** branch ladders
  (`savings_dashboard_service.py:333-409` including the needs_setup copy, and
  `year_end_summary_service.py:2071-2127`). The dispatch itself is
  metadata-flag-driven (not enum/string) -- so the *flag* mechanism is OCP-clean
  (see S6-05) -- but the *duplicated ladder* is the open-closed cost.

- **LSP facet.** Neither dispatcher exposes a common account-calculator
  interface; each branch invokes a different free function with a different
  signature. A new parameterized subtype that matches none of
  interest/amort/investment falls silently through the `else`
  (`savings_dashboard_service.py:393-400`, `year_end_summary_service.py:2127`)
  and is computed as a plain checking account -- a substitutability failure:
  the new subtype is not rejected, it is silently mis-projected.

- **Governing E-NN.** `NONE -> structural-only`. No E-18..E-28 states "one
  dispatcher"; per `phase6_plan.md` section 1 this root is tagged
  `(SRP/OCP)` with no governing E-NN. Recommendation stays structural and does
  not invent a single-source target (contract item 6).

- **Recommended single source of truth (report only).** One
  `account_projection` dispatcher -- a single function mapping an account (via
  its `has_amortization`/`has_interest`/`has_parameters` flags + loaded params)
  to a uniform `(period_id -> Decimal)` result -- consumed by both the savings
  dashboard and the year-end summary, with the `needs_setup` predicate derived
  from the same one place. Branch order and the loan-path representation
  (live `get_loan_projection` vs pre-generated `debt_schedules`) reconciled
  there once.

- **Inherited-vs-independent note.** **Confirmed** from the `phase6_plan.md`
  section-1 register row "Per-account dispatcher (SRP/OCP) -- Dual dispatcher
  `savings_dashboard_service.py:294` vs `year_end_summary_service.py:2036`"
  (carry source `05_symptoms.md:1708-1710`). Re-resolved at live source: the
  `:294` anchor is exact (`_compute_account_projections` begins line 294); the
  `:2036` anchor is exact (`_get_account_balance_map` begins line 2036).
  **Narrowed**: the register said "dual"; this phase finds a *third* partial
  copy (`savings_dashboard_service.py:402-409` needs_setup ladder).

- **Blast radius.** A and B feed two different displayed figures for the same
  account -- the savings-dashboard "projected balance" and the year-end summary
  net-worth/savings-progress numbers -- so a divergence in the loan path
  (live schedule walk vs pre-generated `debt_schedules`) makes the same loan's
  projected balance differ between the two pages. Cross-links the dual-dispatch
  drift surfaced in `05_symptoms.md:1708-1710` (Phase 5 handoff).

## S6-04 -- OCP: `_DEDUCTION_PATH_TYPES` hardcoded enum frozenset for the contribution-path decision is not metadata-flag driven

- **Principle.** Open-Closed -- branch on a metadata flag, not on an
  AccountType enum set (`financial_calculation_audit_plan.md:653-657`);
  ID/flag-for-logic standard (`docs/coding-standards.md:153-155`;
  `00_priors.md:143`).

- **Live branch construct (grepped this session via Explore, line Read in this
  session).**

  ```python
  app/routes/investment.py:58:  _DEDUCTION_PATH_TYPES = frozenset([AcctTypeEnum.K401, AcctTypeEnum.ROTH_401K])
  app/routes/investment.py:289:        is_deduction_path = account.account_type_id in {
  app/routes/investment.py:290:            ref_cache.acct_type_id(t) for t in _DEDUCTION_PATH_TYPES
  app/routes/investment.py:291:        }
  ```

  This is a hardcoded two-member enum frozenset deciding the contribution-setup
  UI path (payroll-deduction vs transfer). It is **not** a metadata flag: a new
  payroll-deduction-funded retirement type (403(b), Roth 403(b), TSP, SIMPLE
  IRA) requires editing this frozenset -- the open-closed violation the audit
  plan's OCP bullet describes.

- **Watchlist rows under test (proven by grep, not roadmap trust).** W-026 /
  W-039 (`00_priors.md:419,432`) -- "Traditional 401(k) and Traditional IRA
  must be identified as pre-tax via metadata flag, not enum dispatch", labeled
  `planned-per-plan`. Live source verdict: the *retirement-gap* pre-tax
  classification **is** now metadata-flag driven --
  `app/services/retirement_dashboard_service.py:159-160`:
  `traditional_type_ids = frozenset(rt.id for rt in retirement_types if
  rt.is_pretax)` -- so for the gap calculator the W-026/W-039 work has
  **landed** (divergence: `planned-per-plan` label understates current source;
  recorded per contract item 4). But the **separate** deduction-path decision
  in `investment.py:289-291` still dispatches on a hardcoded enum set that no
  metadata flag (`is_pretax`/`has_parameters`/...) covers -- the OCP violation
  persists for *that* decision.

- **Sites.** `app/routes/investment.py:58` (definition), `:289-291` (use) --
  Read in full within S6-01's reading of `dashboard`. Contrast control:
  `app/services/retirement_dashboard_service.py:159-160` (Read this session).

- **Governing E-NN.** `NONE -> structural-only` for the deduction-path
  decision (no E-NN states a flag for "is this funded by payroll deduction").
  Per contract item 6 this audit does **not** invent an
  `is_deduction_funded` flag as a single-source target; it records that the
  decision is enum-hardcoded and that no existing flag covers it.

- **Recommended single source of truth (report only).** Report-only: the
  contribution-path decision should key on an account-type metadata attribute
  rather than a literal enum set, consistent with the project's
  IDs/flags-for-logic standard; the specific attribute is a design decision for
  the developer (recorded, not prescribed -- no E-NN governs it).

- **Inherited-vs-independent note.** New this phase as an OCP finding; W-026 /
  W-039 are in the `00_priors.md` watchlist but tagged against the
  retirement-gap path, not this deduction-path branch. Divergence vs the
  `planned-per-plan` label recorded (retirement-gap part is in fact complete;
  the residual is the un-tagged `investment.py` enum set).

- **Blast radius.** No financial figure drifts (this gates a UI prompt path,
  not a calculation); latent structural / correctness-of-routing only --
  a new deduction-funded type would be silently offered the transfer path.
  "No observed drift yet."

## S6-05 -- OCP (verify-confirm + register divergence): metadata-flag dispatch is the dominant pattern; several `planned-per-plan` rows are in fact complete; one residual type-identity lookup has no governing flag

- **Principle.** Open-Closed (`financial_calculation_audit_plan.md:653-657`);
  the contract's "prove the current state by grep, not by trust" mandate
  (`phase6_plan.md` section 2 item 3).

- **Live evidence (Explore sweep this session; counts pasted).** The
  metadata-flag dispatch pattern dominates: **41** distinct
  `has_amortization`/`has_interest`/`is_pretax`/`is_liquid`/`has_parameters`
  call sites across `debt_strategy.py`, `retirement_dashboard_service.py`,
  `savings_dashboard_service.py`, `accounts.py`, `loan.py`,
  `year_end_summary_service.py` (full register returned by the Explore sweep,
  representative anchors Read this session:
  `savings_dashboard_service.py:210,220,229-231,403-409`;
  `year_end_summary_service.py:161,174,2071,2105,2114-2118`;
  `accounts.py:338,361,365,346-348,370-372`;
  `loan.py:92,529,588`; `debt_strategy.py:88`;
  `retirement_dashboard_service.py:160`).

- **Verdict on roadmap dispatch claims (by grep, not trust).**
  - W-012 / W-036 (`00_priors.md:405,429`) -- dispatch to
    `calculate_balances_with_amortization` / `with_interest` when the
    `has_amortization` / `has_interest` flag is set: **holds at current
    source** -- the live dispatchers (S6-03) branch on exactly these flags.
  - W-011 / W-012 cite `chart_data_service.py` as the dispatch location.
    `chart_data_service.py` **does not exist** in the current tree
    (`wc -l app/services/*.py` shows no such file). The dispatch was
    **relocated** to `savings_dashboard_service._compute_account_projections`
    and `year_end_summary_service._get_account_balance_map`. The W-011/W-012
    *code location* is stale; the *behavior* (flag-driven dispatch) holds.
    Divergence recorded per contract item 4.
  - W-021 (`00_priors.md:414`, "replace hardcoded HYSA type-ID with
    `has_interest`", `planned-per-plan`) and W-038 (`:431`, "needs_setup
    unified by metadata flags", `planned-per-plan`): **both complete at
    current source** -- `has_interest` drives interest dispatch
    (`savings_dashboard_service.py:210`, `year_end_summary_service.py:2105`,
    `accounts.py:338,361`) and `needs_setup` is metadata-flag-unified
    (`savings_dashboard_service.py:402-409`). The `planned-per-plan` labels
    **understate** current source -- divergence against the register, recorded
    per contract item 4 (more done than the label claims; not a defect, a
    stale label).

- **Residual type-identity lookup (no governing flag).** The pattern
  `checking_type_id = ref_cache.acct_type_id(AcctTypeEnum.CHECKING)` followed by
  `account.account_type_id == checking_type_id` recurs (Explore sweep, pasted):
  `account_resolver.py:60,121`; `auth_service.py:780`;
  `investment.py:334,336`; `savings_dashboard_service.py:768,771`;
  `year_end_summary_service.py:1724,1726`; `accounts.py:481,483,808,810,1134,1136,1393`;
  `loan.py:537,540`. This is type-identity dispatch on the CHECKING type with
  **no metadata flag** in the schema covering "the primary transactional /
  default-funding account". Recorded as a structural OCP+DRY observation; per
  contract item 6 this audit does **not** invent an `is_primary_checking` flag
  (no E-NN states one). It is also a DRY micro-duplication of the 2-line
  lookup+compare idiom across ~9 files (cross-links the D6-09-style inline
  predicate family in Part 6.1; recorded here as the OCP facet).

- **Governing E-NN.** `NONE -> structural-only`.

- **Recommended single source of truth (report only).** For the confirmed
  flag-driven dispatch: none needed (the flag mechanism is correct; the
  *duplicated ladder* is S6-03's recommendation). For the `checking_type_id`
  idiom: report-only -- a single resolver for "the user's primary checking
  account id" would collapse the ~9-site lookup; the schema-flag question is a
  developer design decision (recorded, not prescribed).

- **Inherited-vs-independent note.** Verify-confirm of the contract's OCP
  "addressed-by-grep" mandate; the W-011/W-012 location staleness and the
  W-021/W-038 label-understates-source divergences are new this phase against
  the `00_priors.md` register (both citations recorded).

- **Blast radius.** No drift from the (correct) flag dispatch. The
  `checking_type_id` idiom: a divergent definition of "primary checking" across
  the ~9 sites would make emergency-fund / savings-progress totals disagree
  about which account is excluded as the checking account
  (`year_end_summary_service.py:156,1724` and
  `savings_dashboard_service.py:768`); no Phase 3/4/5 numeric finding has
  exercised this -- "no observed drift yet".

## S6-06 -- ISP: the 11-key `ctx` dict and the 4-key `base_args` dict are passed whole into helpers that read 1-2 fields

- **Principle.** Interface Segregation -- "Do helpers take large opaque
  parameter bags (`ctx`, `base_args`) when only a few fields are used? List
  cases where this hides what a function actually depends on"
  (`financial_calculation_audit_plan.md:662-664`). The audit plan names `ctx`
  and `base_args` literally.

- **The bag and its field count (Read in full this session).**
  `app/services/year_end_summary_service.py:90-185` `_load_common_data`
  returns a dict with **exactly 11 keys** (:166-185): `year_periods`,
  `all_periods`, `accounts`, `salary_profiles`, `year_period_ids`,
  `debt_accounts`, `savings_accounts`, `investment_params_map`,
  `interest_params_map`, `deductions_by_account`, `salary_gross_biweekly`.

- **Used / total field ratios at each consumer (grepped + Read this session).**
  - `_get_account_balance_map(... ctx: dict | None = None)`
    `year_end_summary_service.py:2041` -- reads `ctx["investment_params_map"]`
    only (`:2119`). **1 / 11.**
  - `_project_investment_for_year(ctx: dict)`
    `year_end_summary_service.py:1033` -- reads `ctx["deductions_by_account"]`
    (`:1060`), `ctx["salary_gross_biweekly"]` (`:1061`). **2 / 11.**
  - `_build_investment_balance_map(base_args: dict, ctx: dict)`
    `year_end_summary_service.py:1572-1573` -- reads
    `ctx["deductions_by_account"]` (`:1623`), `ctx["salary_gross_biweekly"]`
    (`:1624`). **2 / 11.** Plus `base_args` (4 keys: `anchor_balance`,
    `anchor_period_id`, `periods`, `transactions`, built at
    `:2097-2102`) passed whole and then re-splatted `**base_args` at
    `:1602` / `:2109` / `:2127`.
  - `_compute_savings_progress(... ctx: dict ...)`
    `year_end_summary_service.py:894` -- reads `ctx["investment_params_map"]`
    (`:924`), `ctx["interest_params_map"]` (`:925`). **2 / 11.**

- **Sites.** All Read this session: `year_end_summary_service.py:90-185`
  (the builder), `:189-238` (`_build_summary`, the bag's primary consumer
  reading 8 of 11 keys), `:894`, `:1033`, `:1060-1061`, `:1572-1573`,
  `:1623-1624`, `:2041`, `:2097-2102`, `:2119`.

- **Nuance (not a contradiction of W-052).** `phase6_plan.md` section 1 carries
  W-052 ("all loan projection data loaded once and shared across all
  projection consumers", `load_loan_context()`) as a *sanctioned* shared-load
  design. The ISP smell here is **not** the load-once (that is the intended
  pattern); it is passing the *opaque whole 11-key bag* into a helper that
  needs 1 key, hiding the helper's true 1-2-field dependency. The remedy
  preserves load-once and narrows only the parameter surface.

- **Governing E-NN.** `NONE -> structural-only` (W-052 governs the load-once,
  not the parameter surface; no E-NN states the segregation).

- **Recommended single source of truth (report only).** Report-only: pass the
  1-2 fields each helper actually reads (or a small purpose-named struct), not
  the 11-key `ctx`, so each signature declares its real dependency; keep
  `_load_common_data` as the single load point (W-052-consistent).

- **Inherited-vs-independent note.** New this phase; the audit plan names
  `ctx`/`base_args` as the archetype but no section-1 register row or Phase-3
  tag enumerated these specific four consumers and ratios.

- **Blast radius.** No numeric drift (the data is correct; the parameter
  surface only obscures dependency). Latent maintainability/structure risk only
  -- "no observed drift yet".

## S6-07 -- DIP: `amortization_engine.get_loan_projection(params, ...)` duck-types a concrete-model-shaped object instead of accepting a declared DTO (the negative to the `PaymentRecord` positive control)

- **Principle.** Dependency Inversion -- "Do services depend on concrete model
  classes when they could depend on plain-data DTOs? `PaymentRecord` is an
  example of doing this right; find places that do not"
  (`financial_calculation_audit_plan.md:665-667`).

- **Positive controls confirmed at source (this session).**
  `app/services/amortization_engine.py:29-30` `@dataclass(frozen=True) class
  PaymentRecord` and `:82-83` `@dataclass(frozen=True) class
  RateChangeRecord` -- declared plain-data DTOs, passed in as typed lists
  (`generate_schedule(... payments: list[PaymentRecord] | None,
  rate_changes: list[RateChangeRecord] | None ...)`, :335-336). Sibling
  engine functions take **individual primitive values**:
  `interest_projection.calculate_interest(balance, apy,
  compounding_frequency, period_start, period_end)` (AST signature this
  session) -- the W-004 positive (`00_priors.md:397`); and
  `growth_engine.project_balance(...)` per W-007 (`00_priors.md:400`).

- **The negative (Read in full this session).**
  `app/services/amortization_engine.py:864-869`:

  ```python
  def get_loan_projection(
      params,
      schedule_start=None,  # pylint: disable=unused-argument  # kept for callers
      payments=None,
      rate_changes=None,
  ):
  ```

  The docstring (`:893-897`) states `params` is "An object with
  `origination_date`, `term_months`, `original_principal`,
  `current_principal`, `interest_rate`, `payment_day`, and optionally
  `is_arm` attributes (e.g. a LoanParams model instance)". The body reads
  **7 attributes off the duck-typed object** (`params.origination_date`
  :909, `params.term_months` :909, `params.original_principal` :912,
  `params.current_principal` :913, `params.interest_rate` :914,
  `getattr(params, "is_arm", False)` :916; `params.payment_day` is read
  downstream). It accepts the *shape of the `LoanParams` model*, not a
  declared `LoanInputs` DTO -- the exact concrete-model coupling
  `PaymentRecord`/`RateChangeRecord` and `calculate_interest` avoid.

- **Watchlist row under test.** W-005 (`00_priors.md:398`,
  `complete-per-plan`): "`get_loan_projection()` must accept exactly six
  attributes: origination_date, term_months, original_principal,
  current_principal, interest_rate, payment_day." Verdict by Read: the
  function does read those six (plus `is_arm`) -- so the *attribute set* claim
  is satisfied -- but it does so via **duck-typed access on a model-shaped
  object**, not the individual-values / declared-DTO form W-004 establishes for
  the sibling `calculate_interest`. `complete-per-plan` holds for "reads those
  attributes"; it does **not** establish DTO decoupling. Recorded as a
  precise-scope divergence (the label is true as written, narrower than the DIP
  ideal the audit plan asks about).

- **Sites.** `app/services/amortization_engine.py:864-869` (signature, Read in
  full), `:893-916` (attribute reads, Read), `:29-83` (the
  `PaymentRecord`/`RateChangeRecord` positive controls, grepped + Read);
  `app/services/interest_projection.py:49` `calculate_interest`
  (AST signature this session). Call site relying on the model shape:
  `savings_dashboard_service.py:362-366` (`get_loan_projection(acct_loan_params,
  ...)`, Read in S6-03).

- **Governing E-NN.** `NONE -> structural-only` (W-005 governs the attribute
  set, not the DTO boundary; no E-NN states a `LoanInputs` DTO).

- **Recommended single source of truth (report only).** Report-only: define a
  frozen `LoanInputs` dataclass mirroring `PaymentRecord`'s pattern (the
  six/seven fields the engine reads) and have `get_loan_projection` accept it,
  so the engine depends on a declared plain-data contract rather than the
  `LoanParams` model's attribute shape -- consistent with the W-004
  individual-values precedent and the audit plan's stated DIP ideal.

- **Inherited-vs-independent note.** New this phase. Not in the section-1
  register; W-005 is in the `00_priors.md` watchlist but tagged for the
  attribute set, not the DTO boundary (precise-scope divergence recorded).

- **Blast radius.** No numeric drift (`LoanParams` does expose the attributes,
  so it works today); latent structural -- any non-`LoanParams` caller must
  hand-build a model-shaped duck object (cf. the `type("D", (), {...})()`
  adapter pattern at `investment.py:150-155`, evidence the codebase already
  hand-rolls model-shaped objects to satisfy duck-typed engine signatures).
  "No observed drift yet".

### Part 6.2 (SOLID) -- session P6-b complete

Seven `S6-` findings recorded, each carrying every `phase6_plan.md` section-3
element plus the SOLID-specific live metric:

- S6-01 (SRP): `savings.py:dashboard` 470-line claim **does NOT hold** (now
  4 lines, `app/routes/savings.py:107-113`, extracted to
  `savings_dashboard_service.compute_dashboard_data`); the identical
  concern-mix persists un-extracted in `investment.py:dashboard` (295 LOC,
  8 inline ORM queries) and `investment.py:growth_chart` (241 LOC) -- both Read
  in full; contradicts the carried 470-line tag, new violation site recorded.
- S6-02 (SRP, classified non-finding): `amortization_engine.generate_schedule`
  294 LOC but zero concern-mix (no Flask, no `db.session`) -- a length-only
  coding-standard item, NOT an audit-plan SRP violation; recorded so the
  >200-line inventory is complete.
- S6-03 (SRP+OCP+LSP): dual per-account dispatcher **confirmed** from the
  section-1 register at exact live anchors (`savings_dashboard_service.py:294`,
  `year_end_summary_service.py:2036`) and **narrowed** -- a third partial copy
  found (`savings_dashboard_service.py:402-409`).
- S6-04 (OCP): `_DEDUCTION_PATH_TYPES` enum frozenset
  (`investment.py:58,289-291`) is hardcoded, not flag-driven; W-026/W-039
  retirement-gap part is in fact complete (label understates source).
- S6-05 (OCP verify-confirm): metadata-flag dispatch dominates (41 sites);
  W-012/W-036 hold; W-011/W-012 cite the now-deleted `chart_data_service.py`
  (relocated); W-021/W-038 `planned-per-plan` labels understate source
  (complete); residual ~9-site `checking_type_id` type-identity idiom has no
  governing flag (recorded structural, not invented).
- S6-06 (ISP): the 11-key `ctx` dict and 4-key `base_args` passed whole into
  helpers reading 1-2 keys (ratios 1/11, 2/11, 2/11, 2/11); W-052 load-once
  preserved, only the parameter surface flagged.
- S6-07 (DIP): `get_loan_projection(params, ...)` duck-types a `LoanParams`-
  shaped object reading 7 attributes -- the negative to the confirmed
  `PaymentRecord`/`RateChangeRecord` `@dataclass(frozen=True)` and
  `calculate_interest` individual-values positive controls; W-005 holds for the
  attribute set but does not establish DTO decoupling.

Every SRP/OCP claim is backed by the live line count / branch construct Read or
grepped this session, with an explicit verdict on whether the roadmap's
"addressed" / `complete-per-plan` claim holds at current source (S6-01:
stale-but-relocated; S6-03: dual confirmed + third copy; S6-04/S6-05:
label-vs-source divergences recorded per contract item 4; S6-07: precise-scope
divergence). No fix written; no source/test/migration/template/JS touched.
Boundary (Part 6.3, P6-c) is out of this session's scope.

---

# Part 6.3 Boundary -- layering + Transfer Invariant 5 (session P6-c)

Scope (phase6_plan.md section 4 P6-c): the `Routes -> Services -> Models /
Schemas` layering boundary (`CLAUDE.md:95-101`; audit-plan
`financial_calculation_audit_plan.md:671-672`) and the two Transfer-Invariant
structural boundaries -- Invariant 4 "no code path directly mutates a shadow;
all mutations go through the transfer service" and Invariant 5 "balance
calculator queries ONLY `budget.transactions`, NEVER `budget.transfers`"
(`CLAUDE.md:139-140`; priors E-08/E-09 `00_priors.md:344-351`). Per the
contract (phase6_plan.md section 2 item 4) the Phase-3 F-012 verdict is
**re-proven from live source this session, not inherited or quoted**.

Governing E-NN for the boundary part: E-08 (Invariant 4), E-09 (Invariant 5).
The layering rule itself has `NONE -> structural-only` (no E-NN governs it; it
is the architecture statement at `CLAUDE.md:96-100` and audit-plan 6.3).

## B6-01 -- Routes->Services->Models layering: NO Flask-object dependency in `app/services/` (boundary HOLDS; negative finding, fully grep-proven)

- **Principle**: `Routes (Blueprints) -> Services (no Flask imports) -> Models /
  Schemas` (`CLAUDE.md:96-100`); "Services are isolated from Flask -- they take
  plain data, return plain data, never import `request`/`session`"
  (`CLAUDE.md:99-100`); audit-plan `financial_calculation_audit_plan.md:671-672`
  ("Services are forbidden from importing `request`, `session`, `current_app`,
  or any Flask object").
- **Sites / classification**: the 41 `*.py` files in `app/services/` (no
  subpackages) swept this session. The precise word-boundary greps return
  **zero genuine Flask-object dependencies**:

  | Pattern (run this session against `app/services/`) | Result | Verdict |
  | --- | --- | --- |
  | `grep -rn -E '^\s*(from\s+flask\b\|import\s+flask\b)'` | `rc=1` (empty) | no Flask import anywhere in services |
  | `grep -rn -E '\bflask\b'` (any case-sensitive token) | `rc=1` (empty) | -- |
  | `grep -rn -iE '\b(flask\|blueprint\|render_template\|jsonify\|abort(\|redirect(\|url_for()'` | 22 hits, **all docstring/comment prose** ("No Flask imports", "Pure-function service -- no Flask imports", "suitable for a Flask response body") -- e.g. `spending_trend_service.py:9`, `transfer_service.py:20`, `auth_service.py:5`, `year_end_summary_service.py:13` | comments asserting the boundary, **not** importing Flask -- confirms, does not violate |
  | `grep -rn -E '\brequest\s*[.\[]'` | `rc=1` (empty) | no Flask `request` object touched |
  | `grep -rn -E '\bsession\s*\['` | `rc=1` (empty) | no Flask `session[...]` |
  | `grep -rn -E '\bcurrent_app\b'` | `rc=1` (empty) | no `current_app` |
  | `grep -rn -E '(^\|[^a-zA-Z_.])g\.[a-zA-Z_]'` | 5 hits: `budget_variance_service.py:137,138,140,354`, `spending_trend_service.py:153` | **incidental name collision** -- every hit is a comprehension/lambda loop variable (`sum(g.estimated_total for g in groups)`, `lambda g: abs(g.variance)`), NOT Flask's request-context `g`; Flask `g` is never imported (`from flask` grep empty) so the name cannot resolve to it |
  | `grep -rn -E '\bsession\.'` minus `db\.session\.` | 1 hit: `credit_workflow.py:128` -- the word "session" inside the comment "rolls back the SQLAlchemy session" | prose, not Flask `session` |
  | `grep -rn -E '\bdb\.session\b'` | 193 hits | **legitimate** -- the SQLAlchemy ORM session is the Models-layer access the architecture explicitly permits (`Services -> Models (SQLAlchemy)`); not a Flask object |

- **Expanded comparison**: n/a (boundary finding, not DRY).
- **Governing E-NN**: `NONE -> structural-only` (architecture statement, no
  E-NN; honored as the audit-plan 6.3 layering rule).
- **Recommended single source of truth** (report only): none required -- the
  boundary is already singular and intact. The 22 in-service docstrings that
  assert "no Flask imports" are an informal, unenforced contract; a single
  enforced guard (an import-linter / `flake8-tidy-imports` ban on `flask` under
  `app/services/`, or a test that asserts `flask` is absent from every
  `app/services/*.py` AST) would convert the prose contract into a mechanical
  one. Report-only; no fix written.
- **Inherited-vs-independent note**: independent re-proof this session. The
  initial broad sweep mandated by the prompt
  (`from flask import|import flask|request\.|session\[|current_app|g\.`)
  returned ~200 noise hits (`e.g.`, `logging.`, `mfa_config.`, `db.session.`);
  every one was re-run with word-boundary patterns and Read/classified before
  concluding (contract item 1). No prior Phase-3 tag claimed a services-layer
  Flask violation; none found.
- **Blast radius**: no observed drift -- latent structural only. A Flask import
  reaching a service would make that calculation untestable in isolation and
  couple a financial computation to request state; none exists today.

## B6-02 -- Transfer Invariant 5: the balance calculator never reads `budget.transfers` (HELD until `balance:X-bi-6a`, 2026-09-15, rewrote the invariant under R-BAL38: the forward loan plan reads `budget.transfers` for occurrence identity, R-R66; re-proven from live source, NOT inherited from F-012)

- **Principle**: Transfer Invariant 5 -- "Balance calculator queries ONLY
  `budget.transactions`. NEVER also query `budget.transfers`."
  (`CLAUDE.md:140`); prior E-09 (`00_priors.md:348-351`); audit-plan
  `financial_calculation_audit_plan.md:673-675`.
- **Sites / classification** (re-opened at live source this session, F-012
  `03_consistency.md:823-862` deliberately not quoted):
  - `grep -n -E 'Transfer|budget\.transfers|\.transfers\b'
    app/services/balance_calculator.py` -> **2 hits, both docstring prose**:
    `balance_calculator.py:17` ("Transfer effects are included automatically
    via shadow transactions") and `:19` ("The calculator does NOT query or
    process Transfer objects directly"). No `Transfer` model import, no
    `db.session.query(Transfer)`, no `budget.transfers` reference in the
    451-line module.
  - Full import set Read this session
    (`grep -n -E '\bimport\b' app/services/balance_calculator.py`):
    `logging` (`:23`), `OrderedDict` (`:24`), `Decimal, ROUND_HALF_UP` (`:25`),
    `app.services.interest_projection.calculate_interest` (`:27`),
    `app.ref_cache` (`:31`), `app.enums.StatusEnum` (`:32`), and the sole
    function-local import `from app.services.amortization_engine import
    (calculate_monthly_payment, calculate_remaining_months)` (`:202-204`, Read
    in context lines 200-206). **None touch the `Transfer` model or
    `budget.transfers`.**
  - The three callers that feed the balance engine
    (`grep -rn 'from app\.services\.balance_calculator' app/`):
    `calendar_service.py:29`, `grid.py:27`, `dashboard_service.py:27`; each
    invokes `balance_calculator.calculate_balances(...)`
    (`calendar_service.py:482`, `dashboard_service.py:699`, and the grid route)
    passing a `Transaction` list. Shadow effects enter only as ordinary
    `Transaction` rows the caller already filtered -- never via a `Transfer`
    query.
- **Absence proof (pasted, empty results)**:
  - `grep -rn -E 'query\(Transfer\)|get\(Transfer' app/services/balance_calculator.py app/services/interest_projection.py`
    -> `rc=1` (empty). The balance engine and the interest path it imports
    never read `budget.transfers`.
  - `grep -rn -E 'query\(Transfer\)|get\(Transfer|Transfer\(' app/services/`
    minus `transfer_service.py|transfer_recurrence.py|year_end_summary_service.py`
    -> `rc=1` (empty). **No service other than the three Transfer owners
    touches the `Transfer` model at all** -- there is no second
    `budget.transfers` balance reader.
- **The one other-service `budget.transfers` reader, classified**:
  `year_end_summary_service._compute_transfers_summary`
  (`year_end_summary_service.py:636-683`, Read in full this session) does
  `db.session.query(Transfer)...joinedload(Transfer.to_account)...` (`:657-668`)
  and sums `t.amount` into `by_dest[acct_id]["total_amount"]` (`:679`). This is
  a **Section-4 display aggregate** ("Group transfers by destination account
  for the year", docstring `:641`) returned as its own list (`:681-683`). It is
  **NOT a balance read**: it never reaches `balance_calculator.py`, and the
  separate `_compute_net_worth` (`:689-747`, Read this session) does not call
  `_compute_transfers_summary`, so the per-destination `Transfer.amount` total
  is never added to a shadow-derived balance -- the double-count Invariant 5
  guards against cannot occur. Classified **legitimate display aggregate**,
  cross-linked to F-012's note and the P3-d `transfer_amount` register; **not
  an Invariant-5 violation**.
- **Governing E-NN**: E-09 (`00_priors.md:348-351`).
- **Recommended single source of truth** (report only): already singular --
  the balance engine's sole transfer-effect source is the shadow `Transaction`
  rows its callers pass. No consolidation needed; no fix written.
- **Inherited-vs-independent note**: this **confirms** Phase-3 F-012 (verdict
  AGREE, `03_consistency.md:856`) by independent re-greps and full-import Read
  this session -- not inherited. F-012's own cross-link (the
  `_compute_transfers_summary` display total is a P3-d `transfer_amount`
  concern, not a balance violation) is re-verified at live source and stands.
- **Blast radius**: no observed drift. The risk (a transfer counted once as a
  `Transfer` row and again as its two shadows) cannot arise in the balance
  calculator because it never queries `Transfer`; cross-link F-012 / Phase-3
  catalog Gate F4.

## B6-03 -- Transfer Invariant 4: shadows are created/mutated only via `transfer_service` (HOLDS), with ONE structural nuance -- `transfer_recurrence.py:201` deletes a `Transfer` directly, bypassing `transfer_service.delete_transfer`

- **Principle**: Transfer Invariant 4 -- "No code path directly mutates a
  shadow. All mutations go through the transfer service." (`CLAUDE.md:139`);
  prior E-08 (`00_priors.md:344-346`); audit-plan
  `financial_calculation_audit_plan.md:673-675`.
- **Absence proof -- no second shadow constructor / no shadow-link rewrite**
  (pasted):
  - `grep -rn -E '\bTransfer\(' app/ --include='*.py'` minus
    `transfer_service.py` -> a single hit, `app/models/transfer.py:14` (the
    `class Transfer(...)` definition). The only `Transfer(...)` **instantiation**
    in the entire app is `transfer_service.py:361` (Read in context 355-424).
  - `grep -rn -E '\.transfer_id\s*=[^=]' app/ --include='*.py'` -> `rc=1`
    (empty). **No code anywhere repoints a `Transaction`'s shadow link by
    attribute assignment.**
  - The shadow link is set at construction, in exactly one place: the two
    `Transaction(... transfer_id=xfer.id ...)` constructor calls inside
    `transfer_service.create_transfer` (`transfer_service.py:382-385` expense
    shadow, `:403-406` income shadow, Read in full this session). The
    kwarg-form sweep `grep -rn -E 'transfer_id\s*='` confirms every non-`==`
    occurrence is inside `transfer_service.py` (`:220,385,406,428,607,645,655,
    668,682,734,790,845` -- constructor kwargs, `filter_by` shadow lookups, and
    `log_event` kwargs), the model column def (`transaction.py:161`), a log
    format string (`transfers.py:1304`), or the explicit shadow-refusal guard
    (`recurrence_engine.py:419`, below).
  - Every other `Transaction(...)` constructor in `app/`
    (`entry_credit_workflow.py:206`, `credit_workflow.py:232`,
    `recurrence_engine.py:153`, `transactions.py:972,1017` `Transaction(**data)`)
    does **not** pass `transfer_id` (none appear in the `transfer_id=` kwarg
    grep) -- they create non-shadow rows.
- **The Invariant-4 guard is actively enforced at the recurrence boundary**:
  `recurrence_engine.resolve_conflicts` (Read 395-439) refuses to mutate any
  `Transaction` whose `transfer_id is not None`: `recurrence_engine.py:412-426`
  raises `ValidationError("Cannot modify transfer shadow transactions via
  resolve_conflicts. Route transfer mutations through transfer_service.")`
  **before** the mutating lines `:428-431` (`txn.is_override`, `txn.is_deleted`,
  `txn.estimated_amount`) can run. The comment cites "CLAUDE.md Transfer
  invariant 4 / F-007" (`:403`). The `transfer_id=txn.transfer_id` at `:419` is
  a `log_event` kwarg inside that refusal, not a write.
- **Route layer routes every transfer mutation through the service**
  (`grep -n` over `app/routes/transfers.py`): create -> `create_transfer`
  (`:239,920`), delete -> `delete_transfer` (`:502,645,673,1041`), restore ->
  `restore_transfer` (`:558`), update -> `update_transfer` (`:814,1084,1132`).
  No direct `xfer.status_id = / xfer.amount = / xfer.is_deleted = /
  xfer.is_override = / db.session.delete(xfer)` in `transfers.py`. The
  `accounts.py:690-699` account-deletion cleanup (Read 595-714) **queries**
  `budget.transfers` to enumerate rows then deletes each via
  `transfer_service.delete_transfer(xfer.id, current_user.id, soft=False)`
  (`:699`) -- query is a read, mutation is delegated; docstring `:613-615`
  states the intent. The `transactions.py:282` `db.session.get(Transfer, ...)`
  (Read 276-301) is a read-only shadow detection that renders the transfer edit
  form -- no mutation.
- **The one structural nuance (newly found this session, NOT in F-012)**:
  `grep -rn -E 'db\.session\.delete\(xfer\)|...query\(Transfer\)...\.delete\('
  app/` returns exactly two sites: the canonical hard-delete
  `transfer_service.py:661` and **`transfer_recurrence.py:201`**. In
  `regenerate_for_template` (Read in full 141-221), non-overridden
  auto-generated transfers are removed with a bare
  `db.session.delete(xfer)` (`:200-201`) **instead of**
  `transfer_service.delete_transfer(..., soft=False)`. Comparison of the two
  expanded forms:
  - Canonical (`transfer_service.py:661-684`, Read this session): `db.session
    .delete(xfer)` -> `flush` -> **orphan-verify** (`query(Transaction)
    .filter_by(transfer_id=transfer_id).count()`, error-log if `> 0`,
    `:666-674`) -> **`EVT_TRANSFER_HARD_DELETED` audit `log_event`**
    (`:678-684`).
  - `transfer_recurrence.py:200-202`: `for xfer in to_delete:
    db.session.delete(xfer)` -> `flush`. **No orphan verification, no
    `EVT_TRANSFER_HARD_DELETED` audit row** for the deleted transfers (only the
    aggregate `EVT_TRANSFER_RECURRENCE_REGENERATED` event at `:206`).

  The shadow **pair itself stays consistent**: `transaction.transfer_id` carries
  `db.ForeignKey("budget.transfers.id", ondelete="CASCADE")`
  (`transaction.py:161-163`, Read this session), so deleting the parent
  `Transfer` removes both shadow `Transaction` rows atomically at the DB level
  -- Invariants 1/2/3 (paired, never orphaned, amounts equal) are preserved
  exactly as in the canonical path, which relies on the same CASCADE
  (`transfer_service.py:660` comment "rely on ON DELETE CASCADE to remove
  shadows"). The narrow defect is the **literal Invariant-4 wording "All
  mutations go through the transfer service"**: `transfer_recurrence.py:201` is
  a second non-`transfer_service` deletion writer-path into `budget.transfers`
  that skips the service's orphan-verification self-check and the
  `EVT_TRANSFER_HARD_DELETED` forensic audit event. The app does not "directly
  mutate a shadow" here (Postgres CASCADE does the shadow removal), so this is
  a **structural boundary observation, not a balance-correctness drift**.
- **Governing E-NN**: E-08 (`00_priors.md:344-346`).
- **Recommended single source of truth** (report only): route the
  `regenerate_for_template` deletion loop through
  `transfer_service.delete_transfer(xfer.id, template.user_id, soft=False)` so
  the single canonical hard-delete path (orphan self-verify +
  `EVT_TRANSFER_HARD_DELETED` audit) is the only writer-path into
  `budget.transfers` deletions, matching the literal Invariant-4 contract and
  the pattern `accounts.py:698-699` and `transfers.py` already follow. Consistent
  with E-08 (no auditor-invented target; E-08 is exactly "all mutations through
  the transfer service"). Report-only; no fix written.
- **Inherited-vs-independent note**: **independent, newly found this session.**
  F-012 (`03_consistency.md:823-862`) addressed Invariant 5 (balance / double
  count) only; it did not inventory Invariant-4 deletion writer-paths. No
  Phase-3/4 `Phase-6` tag covers `transfer_recurrence.py:201`. The shadow-link
  absence proofs (no second `Transfer(`, no `.transfer_id =`) and the
  `recurrence_engine` guard confirm Invariant 4's creation/mutation half holds;
  the deletion-path nuance is the only divergence and is recorded against the
  literal wording, with the correctness-preserving CASCADE explicitly noted.
- **Blast radius**: no observed financial drift (CASCADE keeps the shadow pair
  consistent and atomic). Latent risk is **forensic, not arithmetic**: a
  template regeneration that deletes auto-generated transfers leaves no
  `EVT_TRANSFER_HARD_DELETED` audit trail and skips the orphan self-check that
  would surface a future FK misconfiguration; cross-link none in Phases 3-5
  (this boundary facet was not previously observed).

### Part 6.3 (Boundary) -- session P6-c complete

Three `B6-` findings recorded, each carrying every `phase6_plan.md` section-3
element plus the boundary-specific presence/absence greps:

- **B6-01** (layering, `NONE -> structural-only`): `Routes->Services->Models`
  **HOLDS** -- zero genuine Flask-object dependencies in all 41
  `app/services/*.py`; every word-boundary sweep empty; the 5 `g.` and 22
  `flask`-token hits classified as loop-variable / docstring noise; 193
  `db.session` uses are the permitted SQLAlchemy Models access. Negative
  finding, fully grep-proven.
- **B6-02** (Invariant 5, E-09): **HELD until `balance:X-bi-6a` (2026-09-15)
  rewrote Invariant 5 under R-BAL38 -- the forward loan plan now reads
  `budget.transfers` for occurrence identity alone (R-R66); historical.** As
  measured then, re-proven from live source not
  inherited -- `balance_calculator.py` had zero `Transfer`/`budget.transfers`
  reads (only 2 docstring-prose hits); full import set Read; absence greps for
  a second balance reader pasted empty; the lone other-service reader
  `_compute_transfers_summary` Read in full and classified a display aggregate
  that never reaches the balance path. Confirms F-012 independently.
- **B6-03** (Invariant 4, E-08): creation/mutation half **HOLDS** (sole
  `Transfer(` instantiation `transfer_service.py:361`; zero `.transfer_id =`
  rewrites; `recurrence_engine.py:412-426` guard actively enforces refusal;
  routes delegate every mutation). **One structural nuance, newly found:**
  `transfer_recurrence.py:201` `db.session.delete(xfer)` bypasses
  `transfer_service.delete_transfer`, skipping the orphan self-verify and the
  `EVT_TRANSFER_HARD_DELETED` audit event; the FK CASCADE
  (`transaction.py:162`) keeps the shadow pair consistent so this is a forensic
  boundary defect, not balance drift. Independent of F-012 (which was
  Invariant-5 only).

Every `app/services/` Flask-object grep hit is classified; every
`budget.transfers` / `Transfer(` touch in `app/services/` and `app/routes/` is
classified legitimate vs violation; the absence-proof greps (no second shadow
constructor, no shadow-link rewrite, no second balance-path `Transfer` reader,
no other-service `Transfer` touch) are pasted with their empty `rc=1` results.
No app run; no source/test/migration/template/JS touched. SOLID (Part 6.2,
P6-b) and the DRY parts (6.1, P6-a1/P6-a2) are out of this session's scope; the
P6-d capstone gate is the next session.

---

# Verification and consolidation gate (session P6-d)

Trust-but-verify capstone. No new structural analysis. Every claim below was
re-resolved to live source this session (`sed -n`/`grep` re-run, not recalled
from a prior session's citation). Tasks per `phase6_plan.md` section 4 (P6-d)
and the acceptance gate section 5.

## 1. Spot-check -- 22 cited sites re-resolved at random across D6-/S6-/B6-

22 sites chosen spanning all three parts (>= 15 required). Each re-resolved with
`sed -n '<line>p' <file>` (or the cited range) this session; "match" = the live
line is the construct the finding asserts.

| # | Finding | Cited site | Re-resolved live content | Verdict |
| --- | --- | --- | --- | --- |
| 1 | D6-01 | `amortization_engine.py:178` | `def calculate_monthly_payment(` (the one formula) | MATCH |
| 2 | D6-01 | `loan_payment_service.py:251,256` | `return amortization_engine.calculate_monthly_payment(` x2 (ARM/fixed) | MATCH |
| 3 | D6-02 | `calendar_service.py:449-450` | `if account.current_anchor_period_id is None:` / `return Decimal("0")` | MATCH |
| 4 | D6-03 | `grid.py:260-279` | inline per-period subtotal loop on `txn.effective_amount`, `status_id != projected_id` | MATCH |
| 5 | D6-05 | `savings_goal_service.py:17-18` | `_PAY_PERIODS_PER_YEAR = Decimal("26")` / `_MONTHS_PER_YEAR = Decimal("12")` | MATCH |
| 6 | D6-05 | `retirement_gap_calculator.py:69` | `net_biweekly_pay * 26 / 12` (bare int literals) | MATCH |
| 7 | D6-06 | `balance_calculator.py:389-419 / 422-451` | `def _sum_remaining` / `def _sum_all`, both bodies `income=Decimal("0.00")`...`return income, expenses` | MATCH |
| 8 | D6-07 | `tax_calculator.py:29` | `TWO_PLACES = Decimal("0.01")` | MATCH |
| 9 | D6-07 | `amortization_engine.py:26` | `TWO_PLACES = Decimal("0.01")` | MATCH |
| 10 | D6-07 (excl.) | `savings_goal_service.py:462-463` | `.quantize(` / `_TWO_PLACES, rounding=ROUND_CEILING` -- the sanctioned exception | MATCH |
| 11 | D6-08 | `credit_workflow.py:229` | `payback_amount = txn.actual_amount if txn.actual_amount is not None else txn.estimated_amount` | MATCH |
| 12 | D6-08 | `grid/_transaction_cell.html:17` | `{% set display_amount = t.actual_amount if t.actual_amount is not none else t.estimated_amount %}` | MATCH |
| 13 | D6-09 | `balance_calculator.py:365` | `if txn.status_id != projected_id:` | MATCH |
| 14 | D6-10 | `retirement_dashboard_service.py:54` | `_DEFAULT_SWR_PCT = Decimal("4.00")` | MATCH |
| 15 | S6-01 | `savings.py:107-113` | 4-line thin delegator -> `savings_dashboard_service.compute_dashboard_data` | MATCH |
| 16 | S6-01 | `investment.py:66-70` | `def dashboard(account_id):` ... `get_or_404` / `abort(404)` (HTTP in body) | MATCH |
| 17 | S6-03 | `savings_dashboard_service.py:294` | `def _compute_account_projections(` | MATCH |
| 18 | S6-04 | `investment.py:58` | `_DEDUCTION_PATH_TYPES = frozenset([AcctTypeEnum.K401, AcctTypeEnum.ROTH_401K])` | MATCH |
| 19 | S6-07 | `amortization_engine.py:864-869` | `def get_loan_projection(` / `params,` / `schedule_start=None,  # pylint: disable=unused-argument` ... | MATCH |
| 20 | B6-02 | `balance_calculator.py:17,19` | docstring prose "included automatically via shadow transactions" / "does NOT query or process Transfer objects directly" | MATCH |
| 21 | B6-03 | `transfer_service.py:361` | `xfer = Transfer(` (the sole instantiation) | MATCH |
| 22 | B6-03 | `transfer_recurrence.py:200-202` | `for xfer in to_delete:` / `db.session.delete(xfer)` / `db.session.flush()` | MATCH |

**Pass count: 22 / 22 (100%).** Threshold (100%) met. No miss; no session
reopened on spot-check grounds.

## 2. Tag-completeness reconciliation

`grep -nE 'Phase-6|DRY note|SRP note'` re-run this session against
`03_consistency.md` (11 hits) and `04_source_of_truth.md` (2 hits). Every hit
mapped below to a `06_dry_solid.md` finding or recorded as reconciled with the
divergence noted (`phase6_plan.md` G6).

| Tag site | Tag substance | Disposition |
| --- | --- | --- |
| `03:763` | DRY note `_sum_remaining`/`_sum_all` collapse | -> **D6-06** (explicitly cited in D6-06 inherited-vs-independent). MAPPED. |
| `03:1562` | E-16: `loan/_escrow_list.html:37` Jinja `comp.annual_amount|float / 12` computes a money value | **No D6-/S6-/B6- finding.** Re-resolved at source this session: `_escrow_list.html:37` = `${{ "{:,.2f}".format(comp.annual_amount|float / 12) }}` -- a SINGLE-site template-computes-money + `|float` E-16 standards violation, NOT a duplication, not SOLID, not a boundary. It is correctly outside the Phase-6 DRY/SOLID/boundary taxonomy (escrow-per-period is produced once, in the template; the defect is float+template-computation, an E-16/coding-standards correctness item Phase 3/5 already verdicted AGREE-numerically on). RECONCILED-WITH-DIVERGENCE: not a Phase-6 structural finding; handed to Phase 8 as an E-16 standards finding (see handoff section 6). P6-a1/a2 scope (duplication) correctly excludes it; no session reopened. |
| `03:2227` | T1 `_transaction_cell.html:17` Phase-6 DRY mirror | -> **D6-08** site 3. MAPPED (explicitly cited). |
| `03:2230` | T4 `_mobile_grid.html:92,179` Phase-6 DRY mirror x2 | -> **D6-08** sites 4/5. MAPPED (explicitly cited; the x2 split is the D6-08 "sharpened to 5 sites" note). |
| `03:2267` | Phase-6 DRY notes: the 4 mirrors S10/S14/T1/T4 | -> **D6-08** (the carried 4 + 1 newly-found site 6). MAPPED. |
| `03:2462` | Q-12/A-12: route-resident financial derivation as a Phase-6 SRP example (loan pre-fill rides F-013) | Duplication facet -> **D6-01** (the loan pre-fill rides the 16-site `monthly_payment` substrate; `loan.py` route call sites are rows in D6-01's register) and **D6-05** (the obligations route-resident aggregation). Route-layer-SRP facet: `obligations.py:summary` is 162 LOC (S6-b inventory) -- BELOW the audit-plan's 200-line SRP bar (`financial_calculation_audit_plan.md:651-652`), so per the audit-plan's own threshold it is correctly not an independent S6 finding; **S6-01** captures the route-resident-derivation SRP pattern at the one site that does exceed the bar (`investment.py`). MAPPED with threshold rationale. |
| `03:3136` | `_calculate_deductions` one parameterized core, pre/post by `timing_id` -- DRY-correct, Phase-6 note | **No D6- finding, correctly.** Re-resolved this session: `grep 'def _calculate_deductions' app/` = exactly ONE definition (`paycheck_calculator.py:403`); `calculate_paycheck` invokes it at `:149` (PRE_TAX) and `:217` (POST_TAX). Phase 3's "DRY-CORRECT (one core, two timing ids)" verdict is accurate at live source -- there is no duplication to find. RECONCILED: a confirmed-compliant negative (analogous to S6-02 / B6-01), recorded here; the only divergence is that P6-a1/a2 scope (canonical-producer-ABSENCE / micro-duplications) did not enumerate the DRY-PRESENT positive, so it is recorded in this gate, not as a new D6 finding. No session reopened (no duplication exists). |
| `03:3171` | Same: POST_TAX timing id shares the one core, DRY-correct | -> same as `03:3136`. RECONCILED (DRY-PRESENT positive; one producer re-verified). |
| `03:3186` | DRY note: `pre_tax_deduction`/`post_tax_deduction` share `_calculate_deductions` -- correct | -> same as `03:3136`. RECONCILED (DRY-PRESENT positive). |
| `03:3707` | F-030 `transfer_amount_computed`, same route-resident-derivation Phase-6 SRP class; Q-12 | -> same family as `03:2462`. MAPPED: duplication facets in D6-01/D6-05; route-SRP facet below the 200-line bar (S6-01 carries the over-bar instance). |
| `03:5328` | W-251 Q-12 (aggregator-owner/SRP, Phase-6, separate) | -> **D6-05** (loan-obligation / monthly-equivalent aggregator + 26/12 factor). MAPPED (explicitly cited in D6-05 inherited-vs-independent). |
| `04:487` | Monthly-payment call-site audit is a Phase-3/Phase-6 matter | -> **D6-01** (the 16-site `calculate_monthly_payment` register). MAPPED (explicitly cited in D6-01). |
| `04:2446` | 0-vs-None at `retirement_dashboard_service.py:224` flagged Phase-3/Phase-6 alongside F-042 (A-26 tail) | -> **D6-10** inherited-vs-independent note explicitly carries "the A-26 `estimated_retirement_tax_rate` NULL-semantics tail (`retirement_dashboard_service.py:222-226`) ... out of structural scope and carried forward unchanged, not resolved here" + Phase-6 handoff section 6. MAPPED to the sanctioned carry (recorded, not dropped). |

**Result:** 13/13 tags reconciled -- 9 MAPPED to a D6-/S6-/B6- finding, 3
(`03:3136/3171/3186`) recorded as a single DRY-PRESENT confirmed-compliant
negative, 1 (`03:1562`) recorded RECONCILED-WITH-DIVERGENCE (E-16 single-site
standards, out of the DRY/SOLID/boundary taxonomy, handed to Phase 8). No tag is
left unmapped and undocumented; no tag's divergence requires a session reopen
(none is a missed duplication -- the only non-mapped item is a single-site
non-duplicated standards issue correctly outside Phase-6 scope).

### Section 1 register consumption (all 11 rows)

| Section 1 register row | Governing | -> Finding | Status |
| --- | --- | --- | --- |
| Loan resolver | E-18 | D6-01 | consumed; carry narrowed up (16 sites vs ">=4") |
| Anchor resolver | E-19 | D6-02 | consumed; carry narrowed up (5th anchor behavior vs F-001's 4) |
| Period subtotal | E-25 | D6-03 | consumed; line range widened :260-279 |
| Balance-as-of-date | E-27 | D6-04 | consumed; confirmed at source |
| Loan-obligation aggregator + 26/12 | E-24 | D6-05 | consumed; factor-site count narrowed up |
| Money-rounding helper | E-26 | D6-07 | consumed; 19 files + 24 sites both exact |
| effective_amount mirror | E-25 family | D6-08 | consumed; sharpened to 5 sites + 1 new |
| Status-filter inline | E-15 | D6-09 | consumed; extended (double `[CREDIT,CANCELLED]`) |
| Magic-number fallbacks | PA-05 | D6-10 | consumed; sharpened (unused-constant/dual-convention) |
| Per-account dispatcher | SRP/OCP | S6-03 | consumed; narrowed (third partial copy) |
| Helper split (`_sum_*`) | DRY | D6-06 | consumed; sharpened (byte-identical, not "vary by filter") |

**11/11 register rows consumed.** Mandated repository-wide sweeps were swept,
not sampled: D6-07 carries the full 19-file `TWO_PLACES` table + the classified
24-site monetary `.quantize()` table (+ the ~99 explicit-mode and the excluded
non-monetary/`ROUND_CEILING` sites); D6-08 carries the full 6-site mirror
register (5 carried + 1 new); D6-09 the full inline-status register
(i/ii/iii/iv); D6-10 the full 4-literal register; S6-05 the 41-site flag-dispatch
register; B6-01/B6-02/B6-03 the empty-result absence greps pasted. Sweep
completeness confirmed.

## 3. E-NN consistency roll-up (per D6- finding)

| D6- | Governing E-NN | Recommended single source consistent with E-NN? | No invented target? |
| --- | --- | --- | --- |
| D6-01 | E-18 | YES -- "one event-derived resolver replaying confirmed payments from latest anchor" verbatim E-18 | YES |
| D6-02 | E-19 | YES -- "one date-anchored anchor resolver, NULL unreachable, resolved on read" verbatim E-19 | YES |
| D6-03 | E-25 | YES -- "one period-subtotal producer sharing `_entry_aware_amount`" verbatim E-25 | YES |
| D6-04 | E-27 | YES -- "one canonical entries-aware balance-as-of-date, anchor-resolved per E-19"; effective-date rule explicitly flagged E-27's own open detail | YES |
| D6-05 | E-24 | YES -- "one monthly-equivalent aggregator, skip-ONCE/skip-`end_date<today`; `_PAY_PERIODS_PER_YEAR`/`_MONTHS_PER_YEAR` imported not re-inlined" verbatim E-24 | YES |
| D6-06 | E-25 family / NONE->structural-only | YES -- collapse to one parameter-free helper; explicitly `NONE->structural-only` for the `_sum_*` collapse (no E-NN names a single-`_sum_*` target -- correctly NOT invented) | YES |
| D6-07 | E-26 | YES -- one `round_money` (2dp ROUND_HALF_UP), named sanctioned variants, full-precision intermediates -- verbatim E-26 | YES |
| D6-08 | E-25 family + E-10/E-15 | YES -- existing `Transaction.effective_amount` is the canonical accessor; no new accessor invented | YES |
| D6-09 | E-15 family / NONE->structural-only | YES -- E-15 (ID-based) satisfied; centralization is explicitly `NONE->structural-only` (no E-NN names a status-predicate target -- correctly NOT invented) | YES |
| D6-10 | PA-05 / NONE | YES -- promote existing `_DEFAULT_SWR_PCT`/`_DEFAULT_RETURN_PCT` + one converter; no correctness-value invented | YES |

Every D6- recommended single source is consistent with its governing E-NN and
invents no target no E-NN states (the three `NONE->structural-only` cases --
D6-06 `_sum_*`, D6-09 status predicate, plus the S6-/B6- structural finds --
explicitly decline to invent an E-NN, per contract item 6).

**Sanctioned exceptions excluded as findings (confirmed):**

- **E-26 `ROUND_CEILING`**: `savings_goal_service.py:462-463` (`_compute_
  required_monthly`, `.quantize(_TWO_PLACES, rounding=ROUND_CEILING)`)
  re-resolved at source this session (spot-check row 10) -- D6-07 section (d)
  explicitly **EXCLUDES** it as the documented E-26 exception, not a finding.
  Confirmed excluded.
- **E-28 anchor-balance CHECK**: the `accounts.current_anchor_balance` /
  `account_anchor_history.anchor_balance` absent-range-CHECK sanctioned domain
  exception is not raised as a D6-/S6-/B6- finding anywhere in Parts 6.1-6.3
  (`grep -n 'E-28\|current_anchor_balance.*CHECK'` over the findings = no finding
  asserts it as a violation). Confirmed not relitigated.

## 4. Inherited-vs-independent roll-up

| Finding | Disposition vs section-1 register / Phase-3 tags |
| --- | --- |
| D6-01 | CONFIRM + NARROW-UP (16 sites vs ">=4"; F-013 carry undercounted -- recorded as divergence-against-prior-phase per audit-plan 10.8) |
| D6-02 | CONFIRM + NARROW-UP (5th anchor behavior `calendar_service.py:449-450` vs F-001's four) |
| D6-03 | CONFIRM (line range widened :263-279 -> :260-279, recorded) |
| D6-04 | CONFIRM (no-`selectinload(entries)` + period-selection slice resolve exactly as E-27 substrate) |
| D6-05 | CONFIRM + NARROW-UP (2 extra 26/12 inlinings vs E-24's 2 named) |
| D6-06 | CONFIRM + SHARPEN (byte-identical, not the carried "vary by filter") |
| D6-07 | CONFIRM (19 files + 24 sites both exact, no narrowing) |
| D6-08 | CONFIRM + SHARPEN (5 sites vs carried "4"; +1 new site 6, scope-reason noted -- not a contradiction) |
| D6-09 | CONFIRM + EXTEND (double `[CREDIT,CANCELLED]` re-centralization + grid->mobile byte-dup, Phase-3 did not tag) |
| D6-10 | CONFIRM + SHARPEN (named constants DO exist but unused/dual-convention -- worse-for-drift than PA-05's "absent") |
| S6-01 | **CONTRADICT** the carried roadmap 470-line `savings.py:dashboard` tag (now 4 lines, claim stale/no-longer-resolves) -- both citations recorded; the live violation (`investment.py:dashboard`/`growth_chart`) is NEW this phase |
| S6-02 | NEW (classified non-finding so >200-line inventory complete) |
| S6-03 | CONFIRM (exact anchors `:294`/`:2036`) + NARROW (third partial copy `:402-409`) |
| S6-04 | NEW OCP; W-026/W-039 `planned-per-plan` label understates source (retirement-gap part complete) -- divergence recorded |
| S6-05 | VERIFY-CONFIRM; W-011/W-012 cite deleted `chart_data_service.py` (relocated); W-021/W-038 `planned-per-plan` understates source -- divergences recorded both citations |
| S6-06 | NEW (audit-plan names `ctx`/`base_args` archetype; no prior row enumerated the 4 consumers/ratios) |
| S6-07 | NEW; W-005 `complete-per-plan` holds for attribute-set but not DTO decoupling -- precise-scope divergence recorded |
| B6-01 | INDEPENDENT re-proof (no prior Phase-3 services-layer Flask tag; none found) |
| B6-02 | CONFIRM F-012 independently (re-greps + full-import Read, not inherited) |
| B6-03 | INDEPENDENT NEW (F-012 was Invariant-5 only; `transfer_recurrence.py:201` Invariant-4 deletion-path nuance newly found) |

**Contradictions of a prior phase are Phase-6 findings with both citations:**
S6-01 contradicts the roadmap/Phase-3 470-line `savings.py:dashboard` tag
(`financial_calculation_audit_plan.md:650-652` carried vs live
`app/routes/savings.py:107-113` 4-line delegator) -- recorded in S6-01's
inherited-vs-independent note with both citations. The S6-04/S6-05/S6-07
label-vs-source divergences (W-026/W-039, W-011/W-012, W-021/W-038, W-005) and
the D6-01/D6-02/D6-05 carry-undercounts are recorded per contract item 4 /
audit-plan 10.8 with both citations in each finding's note.

## 5. Acceptance gate G1-G9 (`phase6_plan.md` section 5)

| Gate | Criterion | Evidence | Verdict |
| --- | --- | --- | --- |
| **G1** | File exists, non-empty; 3 parts (DRY 6.1, SOLID 6.2, boundary 6.3), every finding all section-3 elements | `06_dry_solid.md` = 2007 lines pre-gate; Part 6.1 (D6-01..D6-10), Part 6.2 (S6-01..S6-07), Part 6.3 (B6-01..B6-03); each finding carries principle+citation, sites, expanded comparison (DRY), governing E-NN, recommended SoT, inherited-vs-independent, blast radius | **PASS** |
| **G2** | Every site cites file:line Read/grepped during a Phase-6 session; none sourced only from a Phase-3/4 tag without re-resolution | Spot-check task 1: 22/22 random sites re-resolve at live source this session; each finding's "Grep/sites (this session)" block names the command run | **PASS** |
| **G3** | Every DRY finding shows expanded-form comparison; every "N places" claim has N citations or is marked incomplete | D6-01..D6-10 each carry an "Expanded comparison" block with the duplicated lines; D6-01 16/16 sites pasted, D6-07 19/19 + 24/24 tables, D6-08 6/6, D6-09 i-iv full register, D6-10 4/4 | **PASS** |
| **G4** | Every SRP/OCP finding shows the live metric + explicit verdict on roadmap "addressed"/`complete-per-plan` -- by grep not trust | S6-01 (`wc -l savings.py`=288, dashboard now 4 lines -> 470-claim does NOT hold; investment.py 295/241 LOC); S6-03 (145/93 LOC, anchors exact); S6-04/S6-05 (W-row label-vs-source verdicts); S6-07 (W-005 precise-scope verdict) -- all with live metric | **PASS** |
| **G5** | Spot-check >= 15 sites, 100% resolve; table + count shown | Task 1: 22 sites (>= 15), 22/22 = 100%, table + count shown | **PASS** |
| **G6** | Every Phase-6/DRY note/SRP note tag in 03/04 maps to a finding or recorded superseded with divergence; section-1 register fully consumed; sweeps swept not sampled | Task 2: 13/13 tags reconciled (9 mapped, 3 DRY-PRESENT negative, 1 reconciled-with-divergence handed to Phase 8); 11/11 register rows consumed; full sweep registers present | **PASS** |
| **G7** | Each D6- SoT consistent with governing E-NN; E-26 ROUND_CEILING + E-28 anchor-CHECK excluded; no fix diff (or reverted + recorded) | Task 3: 10/10 D6- consistent, no invented target; E-26 `savings_goal_service.py:462-463` excluded in D6-07(d); E-28 not raised as a finding anywhere; no diff produced in any P6 session (recommendations are prose-only) | **PASS** |
| **G8** | No new auditor-invented "obvious" single-source expectation added to `09_open_questions.md`; only genuinely new ambiguities | `git status` shows `09_open_questions.md` NOT modified (only `06_dry_solid.md` changed); the `NONE->structural-only` findings explicitly decline to invent an E-NN | **PASS** |
| **G9** | `git status` shows only `docs/audits/financial_calculations/` changed; no source/test/migration/template/JS touched | `git status --short` = `?? docs/audits/financial_calculations/06_dry_solid.md` only (task 7) | **PASS** |

**G1-G9: 9/9 PASS.**

## 6. Handoff to Phases 7 / 8 / 9

- **Phase 7 (test gaps).** Each D6- consolidation implies a cross-site
  equivalence test: D6-01 -- one loan resolver, asserted-equal `(balance,
  monthly_payment, schedule)` across all 16 call surfaces; D6-02 -- one
  anchor-None behavior across the 6 producers; D6-03 -- `balance[p]-balance[p-1]
  == subtotal.net` on the same grid; D6-04 -- calendar month-end == canonical
  balance-as-of-date for the same account/date; D6-05 -- `compute_committed_
  monthly` and `/obligations` agree (the `end_date<today` filter); D6-06 --
  property-equality of the two `_sum_*` bodies; D6-07 -- a `round_money`
  golden-cents test over the 24 bare sites; D6-08 -- the 6 mirrors equal
  `Transaction.effective_amount`; D6-09 -- one status predicate; D6-10 -- SWR
  fraction/percent round-trip. S6-03 -- a dispatcher-equivalence test
  (savings-dashboard vs year-end loan path). B6-01/B6-02 -- an enforced
  `flask`-absent / `Transfer`-absent AST/import test (B6-01 explicitly
  recommends converting the prose contract to a mechanical one).
- **Phase 8 (findings + severity).** D6-/S6-/B6- feed the structural findings;
  severity assigned in Phase 8, not here. Additionally carried to Phase 8 as a
  standards finding (NOT a Phase-6 structural one): the `03:1562` E-16
  `loan/_escrow_list.html:37` `comp.annual_amount|float / 12` -- a single-site
  template-computes-money + `|float` violation, re-resolved at source this
  session, outside the DRY/SOLID/boundary taxonomy; Phase 3 verdicted it
  AGREE-numerically with the E-16 cross-link. Recorded here so it is not
  dropped.
- **Phase 9 (open questions).** No new auditor-invented single-source
  expectation added (G8). No genuinely new ambiguity surfaced by Phase 6 --
  **"none, mirrors Phase 5 G8."**
- **A-26 tail carried forward unchanged (recorded, not dropped).** The
  `estimated_retirement_tax_rate` NULL-semantics question
  (`retirement_dashboard_service.py:222-226`; the 0-vs-None conflation at
  `:224`; `04:2446` / Q-26 / F-042 family; model comment
  `app/models/user.py:215-216` vs `retirement_gap_calculator.calculate_gap:76,
  108` actual behavior) remains out of Phase-6 structural scope. It is
  developer-adjudicated (hard rule 5), surfaced in D6-10's inherited note, and
  passed to Phase 8/9 exactly as inherited -- not resolved, not dropped.

## 7. git status

```
$ git status --short
?? docs/audits/financial_calculations/06_dry_solid.md
$ git branch --show-current
dev
```

Only `docs/audits/financial_calculations/06_dry_solid.md` is changed (untracked
-- the Phase-6 output file is new). No source, test, migration, template, or JS
file touched. G9 confirmed.

---

## Phase 6 complete

All P6-d tasks executed; the acceptance gate roll-up:

- **G1 PASS** -- three parts, every finding all section-3 elements.
- **G2 PASS** -- every site re-resolvable; 22/22 spot-check at live source.
- **G3 PASS** -- expanded comparisons present; every "N places" has N citations.
- **G4 PASS** -- SRP/OCP live metrics + roadmap verdicts by grep (the 470-line
  `savings.py:dashboard` claim proven stale at current source: now 4 lines).
- **G5 PASS** -- 22 spot-checked, 100% resolve.
- **G6 PASS** -- 13/13 Phase-6/DRY/SRP tags reconciled; 11/11 section-1 register
  rows consumed; mandated sweeps swept not sampled.
- **G7 PASS** -- every D6- SoT consistent with its governing E-NN; E-26
  `ROUND_CEILING` and E-28 anchor-CHECK sanctioned exceptions excluded; no fix
  diff produced (recommendations prose-only).
- **G8 PASS** -- no auditor-invented single-source target; `09_open_questions.md`
  untouched.
- **G9 PASS** -- `git status` shows only `docs/audits/financial_calculations/`.

**9/9 gates PASS. Phase 6 (DRY / SOLID / boundary) is complete.** 23 findings
recorded (D6-01..D6-10 DRY, S6-01..S6-07 SOLID, B6-01..B6-03 boundary), each
trust-but-verify-grounded at live source. The single non-mapped tag (`03:1562`
E-16 escrow-template arithmetic) is correctly outside the Phase-6 structural
taxonomy and is carried to Phase 8 as a standards finding, not dropped. The
A-26 `estimated_retirement_tax_rate` NULL-semantics tail is carried forward
unchanged. No source, test, migration, template, or JS file was modified in any
Phase-6 session.
