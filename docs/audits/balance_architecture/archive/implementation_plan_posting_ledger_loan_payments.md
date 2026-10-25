> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Implementation plan: post confirmed loan payments with their REAL principal / interest / escrow split

**Status:** ALL 7 COMMITS DONE ON THE FEATURE BRANCH (local, not pushed/PR'd) as of 2026-07-01;
developer-gated manual prod-clone verification (§9.7) + `dev -> main` PR remain. (2026-06-30, v2 -- full
rewrite after the adversarial review in `adversarial_review_posting_ledger_loan_payments.md`.) The
Commit-1-through-6 arc (C1-C4 + temporal-escrow T1/T2 + T3 wiring + T4 backfill) plus the T5
reconciliation oracle are all SHIPPED to the branch; see the T3 / T4 / T5 paragraphs below for the
as-built detail and Section 9 for the per-commit record. **Commits 1-4 SHIPPED to the feature branch
(as-built; local, not pushed/PR'd):** C1 `4d5d0ff` (ref tables -- Section 4.1), C2 `c26a899` (the
`budget.ledger_accounts` schema -- Section 4.2; migration head is now `efca4315bf81`), C3 `c3937ce`
(the chart resolver `get_or_create_loan_ledger_account` -- Section 4.2 / Section 9 item 3), C4 `f4992d0`
(the pure split + posting service -- Section 6 / 7 / 9 item 4). **As-built deviations from the original
spec, all developer-ratified:** (1) the loan shape CHECK shipped columns-only (see D14 and Section
4.2(4); the original `kind_id IN (...)` form was not implementable); (2) the C3 resolver ALSO guards
that the loan account classifies AMORTIZING (D15 / Section 4.2), beyond the plan's stated class/kind
guard; (3) C4 ships in a NEW `app/services/loan_posting_service.py` sibling module (not `posting_service`
as the plan said -- that file would exceed the 1000-line module gate), reusing posting_service's shared
balanced-write primitives; (4) C4 escrow uses `calculate_monthly_escrow(components)` with **NO
inflation** (developer decision -- the plan's `(components, payment_date)` form would diverge the
principal from the cash actually paid and from the resolver on-schedule, breaking the parallel-run
oracle, whenever an active escrow component carries an inflation_rate; the entire app already models
payment-component escrow without inflation); (5) C4 also unified the monthly-interest accrual into one
`app/utils/money.accrue_monthly_interest` (4 inline copies -> 1) so the split's formula is byte-identical
to the resolver by construction.

**PREREQUISITE INSERTED (2026-07-01, before Commit 5): temporal escrow.** A review of C4 found the split
computed escrow from the CURRENT config and applied it to every past payment, so an escrow change would
retroactively mis-split settled history (the balance drifts). Per three developer decisions, the fix was
taken NOW, the fully-normalized way -- effective-date the escrow config (`[effective_date, end_date)`
range columns on `EscrowComponent`). This became a two-commit prerequisite that lands BEFORE the wiring,
tracked in the companion plan `implementation_plan_temporal_escrow.md`: **T1** (`ac409b5` -- the schema,
migration `d1e7c4a2f9b3`, all escrow consumers switched off `is_active`) and **T2** (`ce76a03` -- the
split now sums each payment's escrow as-of its pay-period start via
`EscrowComponent.is_active_on` + `load_all_escrow_components`, immutable for a past date). Both SHIPPED
to the feature branch (local, not pushed). **So the original Commits 5-7 below become T3-T5** and now sit
after this prerequisite; C4's escrow paragraph above is superseded by the effective-dated split (T2).

**T3 (original Commit 5 -- lifecycle wiring) SHIPPED (`45fa001`, local, not pushed):** the C4 split
service is now wired into every chokepoint that changes a loan's confirmed payments -- settle / revert /
amount+actual edit / delete / restore (`transfer_service`, via a NEW
`app/services/_transfer_loan_posting.py` sibling extracted to keep `transfer_service` under the
1000-line module gate, mirroring the `_transfer_ownership.py` split), the balance true-up
(`anchor_service`), the ARM-rate and origination-rate / `payment_day` edits, and the params-create N1
back-post (loan routes). **As-built additions, developer-ratified:** (6) the true-up and both rate paths
share ONE new `loan_posting_service.sync_all_scenarios_or_duplicate` helper (flush-not-commit; runs the
all-scenario re-split and translates the pending anchor / rate row's same-day / effective-date unique
violation to a bool) -- the DRY resolution of a `duplicate-code` gate finding that the identical inline
sync in `anchor_service` + `escrow_rates` crossed; the duplicate-idempotency is preserved because the
helper's first query autoflushes the pending row, surfacing the collision inside its one `try`; (7)
shared loan-posting TEST helpers (`create_loan_with_trueup` + the ledger / correction query helpers)
were extracted into `tests/_test_helpers.py` and the C4 suite delegated to them (one C4 test's premise
-- "a settle does not auto-post" -- changed under the wiring, so it now drives a projected shadow).
`code-reviewer` CLEAN (no CRITICAL/HIGH/MED); `pylint app/ scripts/` 10.00; full suite 6739. T5
pending (T4 SHIPPED -- see the next paragraph). **Build-Order Step 4** of Option D
(`level1_level2_scope_and_fitness.md`, build-order item 4:
"Post confirmed loan payments with their real principal / interest split; retire the read-time replay
of confirmed history").

**T4 (original Commit 6 -- historical backfill) SHIPPED (local, not pushed):** the one-time,
production-wide backfill that posts the real-split correction for every confirmed post-anchor settled
loan payment that predates the T3 go-forward wiring. **The plan's raw-SQL `NOT EXISTS`/`ON CONFLICT`
sketch (Section 9.6, like the Step-2/3 cash backfills) is not implementable for loans** and the
developer ratified **Option A** instead (2026-07-01): the loan split is a running-balance walk over
rate periods + effective-dated escrow, not a one-line SQL formula, so it must reuse the go-forward
split service (built on `ref_cache` + services) -- which the migration host lacks by design
(`create_app(init_ref_cache=False)`, the `3104f87` fix; the `db239773c2fd` self-contained-backfill
policy). So the backfill runs in the POST-migration deploy hook
(`scripts/init_database.py::backfill_loan_payment_postings_after_migration` -> the idempotent
`loan_posting_service.backfill_all_loan_payment_postings`, reusing
`sync_loan_payment_postings_all_scenarios` over every loan via the new
`loan_payment_service.load_all_loan_account_ids`; backfill == go-forward by construction,
reconcile-to-target so it never double-posts), and a thin boundary migration `e2a9f1c7b4d6` (new head,
down_revision `d1e7c4a2f9b3`) has a documented NO-OP upgrade and owns only the reversible raw-SQL
teardown (`source_kind = loan_payment` entries + per-loan `loan_account_id IS NOT NULL` ledger
accounts) -- required so the Commit-2 (`efca4315bf81`) schema downgrade stays clean. Tests
(`tests/test_integration/test_loan_posting_backfill.py`, 14, incl. a deploy-hook commit-contract test
proven non-vacuous by a separate-connection read + a drop-the-commit mutation); executable Alembic
up/down round-trip verified on the rebuilt template; `pylint app/ scripts/` 10.00; `code-reviewer` no
Crit/High (its M1/L2/L3 all fixed). See Section 9.6 as-built for detail.

**T5 (original Commit 7 -- the reconciliation oracle) SHIPPED (local, not pushed):**
`tests/test_integration/test_posting_ledger_loan_reconciliation.py` (12 tests). Deliberately
NON-DUPLICATIVE (the split VALUES live in `test_loan_posting_service.py`, the lifecycle / CRITICAL
full-cash-reversal regression in `test_loan_posting_wiring.py`, the backfill idempotency / deploy-hook
contract in `test_loan_posting_backfill.py`); this oracle adds the reconciliation-level checks those do
not make, mirroring how the Step-3 cash oracle sits above the cash lifecycle / backfill suites. What it
proves: **(8.2) the parallel run vs the resolver** -- the ledger's `anchor - account_posting_total(loan)`
agrees to the penny with `resolve_account_loan(...).current_balance` on-schedule (cash == the resolver's
`monthly_payment`) and DIVERGES by exactly the extra / short principal off-schedule, with the ledger
equal to the hand-computed real balance (the resolver is an independent producer that never reads the
ledger -- so this is what pins the split VALUE); **(8.7)** the loan-aware superseding invariant
(`account_posting_total(loan) == settled_transfer_effect(loan) - non-principal corrections`), driven
where the Step-2/3 cash per-account invariant provably breaks; a rich fixture booking interest + escrow
+ payoff-refund legs together (8.1/8.3/8.4); scenario + owner isolation of the whole sweep (8.8);
backfill == go-forward reconciles identically (8.8); and **two non-vacuity proofs** (a tampered
`actual_amount` breaks the invariant; an injected leg breaks the trial balance) (8.9). A production-wide
sweep helper (`_assert_loan_reconciles`) ties linked + per-loan + completeness + per-entry + trial
together after every fixture; its identities are STRUCTURAL (reader/scenario-scope integrity), always
PAIRED with a value assertion (parallel-run or literal) that pins the split. **As-built additions,
developer-ratified:** (1) a **pre-anchor boundary test** -- a payment that came due before the latest
anchor keeps its Step-2 cash on the linked ledger with NO Step-4 correction (the split excludes it), so
the naive `anchor - linked_net` reads LOW by exactly that cash; the test pins this as the read switch's
"pre-anchor cleanup" (Section 10) boundary, distinct from an off-schedule divergence, and confirms the
reconciliation IDENTITIES still hold with pre-anchor cash present (the `code-reviewer` "strongest
residual risk", closed). **Non-vacuity proven the gold-standard way:** injecting a `+$10` interest bug
into the split service failed 9 of the (then 11) tests -- all four parallel-run tests caught it via the
independent resolver, plus every hand-computed literal -- while the two invariant-only meta-tests
correctly survived (they assert a tamper/injection is caught, independent of the split value). Verified:
full suite **6765 passed** (run alone; baseline 6753 + 12); `pylint app/ scripts/` 10.00 (the oracle is a
test file -- `app/`/`scripts/` unchanged); `shekel-decimal-from-float` clean; `code-reviewer` no
Crit/High (its MEDIUM pre-anchor + MEDIUM sweep-oversell docstring + LOW income-only-note + LOW
exact-value all fixed). **Remaining (developer-gated): the manual prod-clone verification (Section 9.7)
and the `dev -> main` PR.**

**This rewrite supersedes the v1 "contractual mirror" plan.** v1 derived each payment's split from the
loan resolver's *scheduled* amortization and proved `ledger == resolver`. The review showed that
(a) shipped a cash-ledger corruption bug, (b) stored the *contractual* split (which ignores the actual
cash), so it could never reflect a real extra/short payment and kept the loan true-up alive, and
(c) contradicted Option D's own goal of storing the *real* split. This plan stores the real split,
computed from the actual cash paid, and fixes every defect the review found.

**Scope decision (developer, 2026-06-30):**

- **WRITE-ONLY, real-split, validate-then-switch.** Build the reality-authoritative confirmed loan
  ledger and prove it with a hand-computed oracle plus a parallel run against the resolver. **Reads do
  NOT change in this step** -- displayed loan balances keep flowing through the resolver / `balance_at`
  seam. Switching confirmed reads onto the ledger (the change that retires the resolver's confirmed
  replay and ends the true-up dependency) is the **immediate next step** (Section 10), kept separate so
  the foundation is proven before the read path moves. On today's data the ledger and the resolver are
  penny-identical (Section 2.4), so this step is observ­ably a no-op while establishing the correct
  foundation.
- **Escrow = Expense** (per-loan Escrow Expense ledger account). Matches today's net-worth behavior
  (escrow already leaves checking and is not tracked as an asset), so nothing regresses. Modeling
  escrow as a tracked impound asset is a deliberate future feature (it needs disbursement modeling) and
  is out of scope; the Expense choice can be upgraded later via append-only corrections without
  rewriting history.

**Branch:** `feat/posting-ledger-loan-payments` off `dev`.
**Migration head at planning time:** `7d63529e4300` (verified on the prod-clone dev DB; prod == dev at
this head as of the 2026-06-30 clone).

---

## 1. What this delivers (plain language first)

Today a loan payment is a **transfer**: paying the mortgage writes two shadow `Transaction` rows -- an
expense on Checking (money out) and an income on the loan account (money in). Build-Order Step 2 already
posted that movement as one balanced journal entry: `Checking -1,910.95 / Loan +1,910.95` -- the whole
cash dumped onto the loan, because Step 2 did not know the split. The loan's *real* balance is not read
from that entry; it is recomputed on every screen by the loan resolver, which replays the **scheduled**
payment forward from the latest balance anchor and -- crucially -- **ignores the actual cash you paid**
(`rate_period_engine.py:597-599`).

A loan payment is really **one event with four economic parts**. Your June Mortgage payment ($1,910.95
out the door, 6.875%, $616.99 configured escrow):

```
Checking          -1,910.95   (cash out)
Loan (principal)     +275.14   (debt actually paid down)
Interest expense   +1,018.82   (= balance x rate / 12)
Escrow expense       +616.99   (the configured escrow)
                  ----------
                        0.00
```

Step 2 posted the first two as a 2-leg cash entry. Because a posted entry is **append-only**, Step 4
posts a second balanced **correction** that moves the non-principal off the loan:

```
Loan              -1,635.81   (back out interest + escrow)
Interest expense  +1,018.82
Escrow expense      +616.99
                 ----------
                       0.00
```

Now the loan nets to `1,910.95 - 1,635.81 = 275.14` (the real principal), and Step 2's locked cash
record is untouched.

**The one formula that drives everything (this is the change from v1).** The split is computed from the
**actual cash paid**, not the scheduled payment:

> For each confirmed post-anchor payment, walked in due-date order from the latest anchor balance:
> - `interest = round_money(balance_before x rate / 12)` -- accrued on the **real running balance**;
> - `escrow   = calculate_monthly_escrow(components, payment_date)` -- the **configured** escrow;
> - `principal = cash - interest - escrow` -- the **actual** principal, so any extra or short payment
>   lands here;
> - `balance_after = balance_before - principal`.

The correction's legs are `{Loan: -(interest + escrow), Interest: +interest, Escrow: +escrow}`; the
loan's net (Step-2 cash + this correction) is `cash - (interest + escrow) = principal`, so **the cash
automatically determines principal**. The correction itself only needs `interest` (computed) and
`escrow` (configured); the actual cash flows through Step 2.

**Why this is the financially-sound design -- worked examples** (real Mortgage: balance 177,829.83,
6.875%/12 -> interest 1,018.82; configured escrow 616.99):

| Scenario | Cash | interest | escrow | principal | balance drop |
|---|---|---|---|---|---|
| **On-schedule** | 1,910.95 | 1,018.82 | 616.99 | **275.14** | 275.14 (== resolver, == today) |
| **+$500 extra principal** | 2,410.95 | 1,018.82 | 616.99 | **775.14** | 775.14 -- the extra pays down the loan; **no true-up** |
| **-$200 short** | 1,710.95 | 1,018.82 | 616.99 | **75.14** | 75.14 -- honest partial paydown |
| **payoff overpay** (Van) | > payoff | computed | 0 | capped at balance | excess -> **Refund** leg; loan closes at 0 |

On-schedule the split is byte-identical to the resolver and to today's behavior. It diverges only when
the user pays off-schedule -- and there the ledger is correct and self-maintaining, where the resolver is
wrong and needs a true-up.

**This step does NOT:** change how any screen reads a balance (Section 10); touch Step 2's immutable cash
postings; post a loan's opening balance or reconcile pre-anchor history (Section 10); model escrow as an
asset; post anything for an investment or property account.

---

## 2. Verified facts (every claim cited to live code on `dev`)

### 2.1 A loan payment is a Transfer with two shadows; the cash is P&I + escrow

`transfer_service.create_transfer` builds an expense shadow on the from-account and an income shadow on
the to-account (`transfer_service.py:389-397`; `_build_shadow:217-258`, forcing `transfer_id=xfer.id`,
`actual_amount=None`). A loan payment's income shadow sits on the loan; loan code finds them via
`transfer_id IS NOT NULL` + Income type (`loan_payment_service.query_shadow_income:215-259`). The
recurring loan-payment transfer's default cash is `calculate_total_payment(monthly_pi, components) =
round_money(monthly_pi + monthly_escrow)` (`escrow_calculator.py:180-196`;
`routes/loan/payment_transfer.py:71-73`).

### 2.2 The resolver ignores the actual cash (this is what v1 froze, and what we replace)

`replay_schedule` takes `confirmed_payment_dates: list[date]` (`rate_period_engine.py:581`); the
`PaymentRecord.amount` is discarded at `loan_resolver/_periods.py:346-350`. Each step does
`principal = period_pi - interest` (`rate_period_engine.py:553`), never the cash. The module says so:
"The cash amount and escrow are NOT inputs -- only the COUNT and dates of the confirmed payments matter"
(`:597-599`). Consequence: an extra payment does not pay the loan down faster; a short payment still
subtracts the full scheduled principal; the only real-cash signal in a resolved balance is the **anchor
true-up**. Step 4 takes the cash as the authority for principal instead.

### 2.3 The app already stores the real escrow

`EscrowComponent` (`loan_features.py:116-156`) stores named line items (property tax, insurance, ...)
each with an `annual_amount` and optional `inflation_rate`; the monthly figure is
`calculate_monthly_escrow` (`escrow_calculator.py:137-177`). On the real Mortgage the configured monthly
escrow is **exactly 616.99** -- identical to the v1 residual `1,910.95 - 1,293.96` (DB-verified). So
config-based escrow is equally correct on-schedule and strictly more correct off-schedule (it never
absorbs extra principal, fees, or a short payment the way a residual does).

### 2.4 Real data: window is tiny, no divergent cash (the foundation is a no-op today)

On the prod clone (2026-06-30, dev == prod): the ledger holds 103 journal entries / 206 postings, all
two-leg. Mortgage (acct 3): latest anchor `user_trueup 2026-05-22 = 177,829.83`; **one** post-anchor
confirmed payment (pay-period start 2026-05-21, monthly due date **2026-06-01 > anchor**, so it replays
-- the due-date-vs-period-start subtlety handled by `monthly_due_date`), cash 1,910.95, real split
275.14 / 1,018.82 / 616.99, balance 177,554.69. Van (acct 8): latest anchor `user_trueup 2026-06-23 =
15,663.59`; **zero** post-anchor. **No loan payment has `actual_amount != estimated_amount`** -- every
real payment is exactly on-schedule. So Step 4 posts exactly one correction on real data, identical to
v1 and to the resolver; the off-schedule machinery's teeth are entirely synthetic fixtures (Section 8),
as required by "extensible to any data a user could have."

### 2.5 The anchor is the opening balance; only post-anchor payments replay

`LoanAnchorEvent` is append-only (one `origination` + zero-or-more `user_trueup`; immutability listeners
at `loan_anchor_event.py:162-189`); the latest by `(anchor_date, created_at)` is the opening balance
(`loan_resolver/_periods.py:263-296`). Eligibility: `anchor.as_of_date < monthly_due_date(d) and d <=
as_of`, payoff cutoff `if balance <= 0: break` (`rate_period_engine.py:655-665`). Pre-anchor payments
are subsumed by the anchor; posting the opening balance + pre-anchor cleanup are Section-10 work.

### 2.6 Step 2 cash entries already exist; reads stay on the resolver

Step 2's backfill posted every settled non-deleted transfer, including all 6 confirmed loan payments
(3/loan). Step 4 adds corrections; it never edits the cash entries. Loan reads dispatch to the resolver,
never the ledger (`balance_at.py:491-516` -> `net_worth_kernel.generate_debt_schedules:182` ->
`loan_payment_service.resolve_account_loan:535-547`; no posting/ledger import in that chain). So this is
genuinely write-only.

### 2.7 The chokepoints that change a loan's confirmed payments (verified complete)

An exhaustive enumeration of every transfer mutation path confirms the set is complete: each path either
does not change a *confirmed* payment or routes through one of these (`transfer_service.py` unless noted):

| Trigger | Site | Effect |
|---|---|---|
| settle / revert / amount or actual edit (status_id/amount/actual_amount) | `:641` (`update_transfer` END; `_POSTING_RELEVANT_FIELDS = {status_id, amount, actual_amount}` at `:80`) | adds / removes / re-amounts a confirmed payment |
| delete (soft + hard; ad-hoc / regen / account cascade) | `:690` (reverse-before-remove; hard delete at `:714`) | removes a confirmed payment |
| restore | `:951` | re-adds a confirmed payment |
| balance true-up | `anchor_service.apply_loan_anchor_true_up:309` (`LoanAnchorEvent` add `:386`) | re-bases the replay |
| rate change (ARM + base/origination) | `routes/loan/escrow_rates.py:107`; `routes/loan/params.py:196` (`_upsert_origination_rate`) | changes the interest split |
| **loan params first created** (2.8 N1) | `routes/loan/params.py:97` + origination anchor `:112` | makes a previously-unresolvable loan resolvable |

A settled-period move is blocked by the finalised-edit lock (`transfers/mutations.py:61-98`,
`state_machine.py:236-288`; `pay_period_id` is a locked field). Recategorize, scenario change,
recurrence regenerate, template delete, carry-forward, pay-period truncate/reset, and account delete
either cannot touch a confirmed payment or are projected-only (verified). Escrow-config edits need no
hook: escrow is read from config **at post time** per payment_date, so a future config change does not
retroactively move a posted split (and must not -- posted history is immutable).

### 2.8 Defects from the adversarial review, and how this plan resolves them

- **CRITICAL (cash-ledger corruption).** v1 linked the correction by `transfer_id` and put its loan leg
  on the loan's linked ledger. Step 2's cash path reads that ledger via `_posted_net(transfer_id,
  ledger)` with **no `source_kind`** (`posting_service.py:227-237,630`), so it would read `cash +
  correction` and post wrong cash reversals on revert/edit/delete, corrupting Checking with no
  self-heal. **Resolution:** link the correction to the income shadow's **`transaction_id`**, not
  `transfer_id` (Section 5.3). `_posted_net` filters `transfer_id`; the cash transaction path skips
  shadows (`transfer_id is not None` guard, `:733`); so the correction is **structurally invisible** to
  both existing readers. No retrofit of `_posted_net`, no easy-to-forget filter.
- **CRITICAL (wrong foundation).** v1 stored the contractual split. **Resolution:** the real-split
  formula (Section 1 / 6), principal from actual cash.
- **HIGH (escrow mislabel).** v1's `escrow = cash - scheduled_payment` mislabeled extra principal, fees,
  short payments, and payoff overpayment as "escrow." **Resolution:** escrow from config; genuine excess
  -> a Refund leg; underpayment -> honest negative principal (Section 6).
- **HIGH (completeness).** Replayed-row count != confirmed-shadow count (pre-anchor, payoff-truncated,
  future-dated). **Resolution:** the ledger's intended content is defined over the **full** post-anchor
  settled set <= as_of, including post-payoff payments routed to Refund; the oracle checks the full set,
  not just the replayed subset (Section 8). Future-dated settled payments (none today) are flagged for
  the read-switch step, not silently dropped.
- **MED (transfer_id overloading / smell).** Resolved by the `transaction_id` linkage above.
- **MED (ledger_account NULL-pattern straining).** **Resolution:** an explicit
  `ref.ledger_account_kinds` discriminator replaces inferring the row kind from which FKs are NULL
  (Section 5.2), which also serves Section 10's opening-balance (Equity) rows.
- **MED (engine purity / broken passthrough).** v1 threaded a DB id through the pure no-db engine and
  the chain was broken at `replay_schedule`. **Resolution:** no engine change. The split is computed in
  the posting service from the income shadows it already loads, reusing the engine's pure *primitives*
  (`monthly_due_date`, rate lookup, rate periods, anchor selection) -- not its replay (Section 6).
- **MED (reconcile/reverse asymmetry).** **Resolution:** per-payment reconcile keyed by
  `transaction_id`, so delete = reconcile-to-zero (`reverse-before-delete`), mirroring the cash path
  exactly (Section 7). The whole-loan re-sync handles the running-balance coupling.
- **MED (oracle invariant break).** `account_posting_total(loan) == settled_transfer_effect +
  settled_transaction_effect` breaks for a loan once corrections exist (masked today: the test mortgage
  has no `LoanParams`). **Resolution:** the Step-4 oracle supersedes that per-account assertion for loan
  accounts (Section 8).
- **MED (M4 no optimistic lock).** `LoanAnchorEvent` / `RateHistory` have no `version_id`
  (`loan_anchor_event.py`, `loan_features.py`). **Resolution:** rely on single-transaction
  reconcile-to-target; advisory lock noted if concurrency ever matters (solo-user low-risk).
- **LOW (over-wiring).** v1 wired the loan reverse into the payback-delete paths; a loan payment is
  never a credit payback (`credit_workflow.py:305-306,529-540`). **Resolution:** wire the loan reverse
  at `:690` only.
- **LOW (un-wired create_params -- N1).** Added to the chokepoint set (2.7).
- **LOW (reset re-anchor -- N2).** `reset_pay_periods` re-anchors via `stage_anchor_true_up`
  (`pay_period_admin.py:743`), safe only because reset is zero-settled-gated; documented, no wiring
  needed.
- **LOW (settled-period-move re-stamp -- N3).** If a future UI ever moves a *settled* payment's period,
  it must re-stamp the entry (an append-only `entry_date` cannot be reconciled,
  `transfer_service.py:74-77`); documented as a constraint on that future feature.

---

## 3. Design decisions

| # | Decision | Choice | Why |
|---|---|---|---|
| D1 | Split source | **Actual cash**: principal = cash - interest - escrow | The ledger reflects reality (extra/short captured); Option D's "store the real split" goal. |
| D2 | Interest basis | `balance_before x rate / 12` on the **real running balance** (seeded from the anchor, walked with real principal) | Interest accrues on the actual balance, so a prior extra payment correctly lowers later interest. |
| D3 | Escrow source | **Configured** `calculate_monthly_escrow(components, payment_date)` | The app stores it; honest labeling; respects inflation config. |
| D4 | Genuine excess (payoff overpayment) | Principal capped at remaining balance; excess -> per-loan **Refund Receivable** (Asset) leg | The lender owes you a refund; never mislabeled escrow. |
| D5 | Underpayment | `principal` allowed negative (balance rises); surfaced, not absorbed | Honest negative amortization. |
| D6 | Write form | Append a **correction** on Step 2's immutable cash entry | Step 2's cash leg is correct and locked; a correction is how double-entry fixes posted history. |
| D7 | Linkage | Income shadow's **`transaction_id`** + new `source_kind = loan_payment` | Structurally invisible to `_posted_net` and the cash path -> dissolves the CRITICAL bug. |
| D8 | Escrow class | **Expense** (developer choice 2026-06-30) | Matches today's net worth; asset model is a separate future feature. |
| D9 | Row-kind discriminator | Explicit `ref.ledger_account_kinds` column | Replaces the straining NULL-pattern; future-proofs Section 10's Equity rows. |
| D10 | Reconcile granularity | Per-payment target (by `transaction_id`) + whole-loan re-sync for the running-balance coupling; reverse-before-delete | Unifies the lifecycle with the cash path; no special-case reverse. |
| D11 | Split computation home | Posting service, reusing the engine's pure **primitives** (not its replay); no engine change | Keeps the pure engine clean; decouples real-split from the contractual replay. |
| D12 | Reads | **Unchanged** (resolver / seam); the read switch is Section 10 | Validate-then-switch; foundation proven before the read path moves. |
| D13 | Statement-split override | Out of scope (future hook: enter the lender's exact split, computed split as default) | No gold-plating; the computed split is correct for the common case. |
| D14 | Loan shape CHECK scope (developer choice 2026-06-30, **as-built C2**) | **Columns-only**: `ck_ledger_accounts_loan_shape` enforces a per-loan row's column shape but does NOT pin `kind_id` to the loan kinds | A CHECK cannot subquery `ref.ledger_account_kinds`, and the project forbids hardcoding ref IDs (literal IDs would also break model/migration parity). "A loan row's kind is a loan kind" is the **sole writer's** guarantee (`get_or_create_loan_ledger_account` + tests) -- the identical un-CHECKed trust contract `class_id` already carries (`ledger_account_service.py`). So **Commit 3's resolver guard is load-bearing, not belt-and-suspenders.** (As-built C3 `c3937ce`.) |
| D15 | Loan-account validation (as-built C3 `c3937ce`, developer-ratified 2026-06-30) | The resolver ALSO guards that `loan_account_id` classifies **AMORTIZING** (`classify_account`, reads `has_amortization`) -- beyond the plan's class/kind guard -- and loads the loan by `(id, user_id)` (tenancy) but **not** `is_active` | D14's columns-only CHECK cannot verify `loan_account_id` references a loan, so without this a per-loan row could point at a Checking / Credit Card; same load-bearing logic as the kind guard, cheap (`account_type` eager-loaded). Skipping the `is_active` filter lets an archived loan with settled history still resolve (locked by a non-vacuous regression test). |

---

## 4. The chart of accounts and ref additions (additive; no new ledger table)

### 4.1 `ref` additions

- `ref.posting_kinds` += `principal`, `interest`, `escrow`, `refund`.
- `ref.posting_sources` += `loan_payment`.
- `ref.ledger_account_kinds` (NEW table): seed `linked`, `category`, `fallback`, `orphan` (the existing
  four), `loan_interest`, `loan_escrow`, `loan_refund` (and reserve `equity_opening` for Section 10).
- Enums in `app/enums.py`; extend the `_REF_TABLE_SEEDS` lists in `ref_seeds.py`; migration inline-seeds
  with `ON CONFLICT (name) DO NOTHING`. `ref_cache.init()` raises on a missing row in an existing ref
  table -- the Step-3 deploy fix (`create_app(init_ref_cache=False)` for the migration host) already
  covers this; re-verify on the clone.

### 4.2 `budget.ledger_accounts` changes

1. **Add `kind_id`** (FK `ref.ledger_account_kinds`, RESTRICT, NOT NULL). Three-step migration: add
   nullable; backfill from the current NULL-pattern (`account_id` set -> `linked`; `category_id` set ->
   `category`; `is_fallback` -> `fallback`; both-NULL & not fallback -> `orphan`); `alter_column` NOT
   NULL after verifying zero NULLs (raise with the diagnostic SELECT if any survive). The existing
   CHECKs / partial uniques stay (they remain valid); `kind_id` is now the authoritative discriminator
   and every reader keys on it.
2. **Add `loan_account_id`** (FK `budget.accounts`, **RESTRICT**, NULL). Set only on the per-loan
   interest / escrow / refund rows; RESTRICT (not SET NULL) so a loan with ledger accounts cannot be
   deleted -- consistent with "accounts with history are archived, never deleted" and avoiding the
   v1 SET-NULL-orphans-the-kind defect.
3. **`uq_ledger_accounts_loan`** -- partial unique `(user_id, loan_account_id, kind_id)`
   `WHERE loan_account_id IS NOT NULL`. At most one interest, one escrow, one refund account per loan.
4. **`ck_ledger_accounts_loan_shape`** (as-built C2, columns-only -- see D14) --
   `loan_account_id IS NULL OR (account_id IS NULL AND category_id IS NULL AND NOT is_fallback)`.
   It pins the column *shape* of a per-loan row (no account / category link, not the fallback) but does
   **NOT** also constrain `kind_id` to `(loan_interest, loan_escrow, loan_refund)` as this plan first
   proposed: a CHECK cannot subquery `ref.ledger_account_kinds`, and embedding the IDs as literals is
   forbidden (no hardcoded ref IDs) and would break model/migration parity. "A loan row's `kind_id` is a
   loan kind" is therefore the sole writer's contract (the resolver in Section 4.2 below / Commit 3),
   the same un-CHECKed trust `class_id` already carries -- which makes **Commit 3's class/kind guard
   load-bearing** (a writer bug stamping a non-loan kind on a loan row is not caught by the database).

Classes: `loan_interest` / `loan_escrow` are **Expense**; `loan_refund` is **Asset**. The `name` is a
snapshot (`"Mortgage -- Interest"`, etc.) truncated to the column width. Resolver:
`ledger_account_service.get_or_create_loan_ledger_account(user_id, loan_account_id, kind)` -- lazy,
idempotent (by the partial unique; SELECT-then-INSERT relying on the index, not `ON CONFLICT`, mirroring
`get_or_create_category_ledger_account` -- adequate for the solo user, noted).

**As-built (C3 `c3937ce`).** Shipped as specified, with the kind mapped to its class and display
suffix via a module constant `_LOAN_LEDGER_KINDS` (`loan_interest` / `loan_escrow` -> Expense,
`loan_refund` -> Asset; suffixes "Interest" / "Escrow" / "Refund"), idempotency via
`_find_existing_loan_ledger_account`, and the load + validation in `_load_amortizing_loan_account`.
Two additions beyond the original "class/kind-guarded" wording, both developer-ratified: (a) the
resolver rejects any account that does not classify **AMORTIZING** (D15), closing the gap D14's
columns-only CHECK leaves on `loan_account_id`; (b) the tenancy load filters `(id, user_id)` but
deliberately **not** `is_active`, so an archived loan that still carries settled history keeps resolving
its per-loan accounts (locked by a regression test, proven non-vacuous by mutating the filter in). The
snapshot name is `f"{loan.name} -- {suffix}"` clipped to the 100-char column (a very long loan name
clips the suffix -- lossless, since the natural key is the (user, loan, kind) IDs).

### 4.3 No `journal_entries` change

Reuse `transaction_id` (nullable, SET NULL, partial index -- `journal_entry.py:206-214`) for the
correction's linkage to the income shadow, plus `source_kind = loan_payment`. No new column.

---

## 5. Linkage and why it is correct

### 5.1 The correction links to the income shadow's `transaction_id`

Each correction entry: `transaction_id = income_shadow.id`, `source_kind_id = loan_payment`,
`transfer_id = NULL`. The income shadow is the loan-side leg of the payment, so it is the natural anchor
for "this payment's split."

### 5.2 Why this dissolves the CRITICAL bug

- Step 2's cash path reads the loan ledger via `_posted_net(xfer.id, to_ledger.id)`, which filters
  `JournalEntry.transfer_id == xfer.id` (`posting_service.py:227-237`). The correction has
  `transfer_id = NULL`, so **`_posted_net` never sees it** -> cash reversals stay correct.
- The cash transaction path (`sync_transaction_postings`, `reverse_postings_before_delete`) returns
  early for any row with `transfer_id` set (`:733`), so it never posts or reads the income shadow as an
  ordinary transaction -> no double-post, and `_posted_net_by_account(shadow.id)` is never called by the
  cash path.
- Only the new Step-4 reader (`_posted_loan_payment_net(transaction_id)`, filtering `transaction_id ==
  shadow.id AND source_kind = loan_payment`) reads the correction. Disjoint by construction.

A regression test asserts that after a loan correction exists, a revert/delete of the payment posts the
**full** cash reversal (Checking returns to 0), proving `_posted_net` is unaffected.

---

## 6. The real-split computation (`posting_service`, no engine change)

A pure walk over the loan's confirmed post-anchor income shadows, reusing the engine's primitives:

```python
def compute_loan_payment_splits(loan_account_id, scenario_id, as_of):
    """Return the real per-payment split for a loan's confirmed post-anchor payments.

    Reuses the pure primitives (anchor selection, rate periods, monthly_due_date)
    but walks with the ACTUAL cash, not the scheduled payment.  Returns a list of
    LoanPaymentSplit(income_shadow_id, interest, escrow, principal, excess) in
    due-date order.  No DB writes; no resolver replay.
    """
```

Mechanics:

1. `anchor = latest LoanAnchorEvent for the loan` (reuse `_select_latest_anchor`). If the loan is not
   resolvable (no `LoanParams`), return `[]` (N1 guard).
2. `periods = build_rate_periods(...)` (pure; the same period set the resolver builds), for the per-date
   rate.
3. Eligible income shadows: confirmed (settled, non-excluded), `transfer_id IS NOT NULL`, Income type,
   on the loan account, scenario-scoped, with `anchor.as_of_date < monthly_due_date(period_start) and
   period_start <= as_of`, **sorted by due date**. Extract the eligibility predicate into a shared
   helper so it cannot drift from the resolver's (the due-date-vs-period-start subtlety, 2.4).
4. Walk, seeded `balance = anchor.balance`:
   - `rate = period_for_date(periods, period_start).annual_rate`;
   - `interest = round_money(balance * rate / 12)` (zero-rate guarded);
   - `escrow = calculate_monthly_escrow(components, payment_date)`;
   - `cash = shadow.effective_amount` (`COALESCE(actual, estimated)`);
   - `principal = cash - interest - escrow`;
   - **cap / excess:** if `principal > balance`: `excess = principal - balance`, `principal = balance`
     (loan closes); else `excess = 0`. (Underpayment leaves `principal` negative; no clamp.)
   - `balance = balance - principal` (>= 0 after a cap; may rise on underpayment);
   - emit `LoanPaymentSplit(shadow.id, interest, escrow, principal, excess)`.
   - **post-payoff:** once `balance <= 0`, a further confirmed payment has `interest = 0`, `escrow = 0`
     (loan closed), `principal = 0`, `excess = cash` -> routed entirely to Refund (so Step 2's cash leg
     on a closed loan is corrected to a refund, not a phantom paydown). The walk continues to the end of
     the eligible set rather than breaking, so every Step-2 cash entry has a matching correction.

The correction legs for a split, dropping zero legs:
`{loan_linked_ledger: -(interest + escrow + excess) [principal], interest_ledger: +interest [interest],
escrow_ledger: +escrow [escrow], refund_ledger: +excess [refund]}` -- balanced by construction. Worked:
the real Mortgage row -> `{Loan -1635.81, Interest +1018.82, Escrow +616.99}`, loan net 275.14. The
+$500 case -> identical correction legs (interest/escrow unchanged); the loan net is `2410.95 - 1635.81
= 775.14` because the larger Step-2 cash leg flows through to principal.

---

## 7. The posting lifecycle

### 7.1 The service

```python
def sync_loan_payment_postings(loan_account_id, scenario_id):
    """Reconcile a loan's per-payment corrections to the real-split target, idempotently.

    Computes compute_loan_payment_splits(...); for each split, reconciles the
    correction posted under the income shadow's transaction_id to its target legs
    (reverse stale by transaction_id for shadows no longer in the set).  Whole-loan
    because interest accrues on the running balance, so changing one payment
    re-splits the later ones.  Flushes, no commit.
    """

def reverse_loan_payment_postings_for_shadow(income_shadow):
    """Reconcile one payment's correction to zero, before its shadow is deleted.

    Mirrors the cash path's reverse_postings_before_delete: reads the posted
    loan_payment legs for income_shadow.transaction_id and emits a balanced
    reversal, so a hard delete (transaction_id SET NULL) never strands it.
    """
```

Reconcile-to-target per shadow uses `_posted_loan_payment_net(transaction_id)` (the new
`transaction_id AND source_kind = loan_payment` reader); `delta = target - posted`; drop zero legs; emit
one balanced entry if any non-zero (non-zero deltas always sum to zero -> >= 2 legs). Entry header:
`transaction_id = shadow.id`, `source_kind = loan_payment`, `entry_date = _civil_settle_date(
shadow.paid_at, shadow.pay_period)`, scenario/period/owner from the shadow.

### 7.2 Wiring (the chokepoints, 2.7)

- **Settle / revert / amount-or-actual edit / restore.** At `transfer_service.py:641` (update END) and
  `:951` (restore), when `classify_account(xfer.to_account)` is AMORTIZING, call
  `sync_loan_payment_postings(xfer.to_account_id, xfer.scenario_id)` LAST (after the cash sync).
- **Delete.** At `:690`, before removal, call `reverse_loan_payment_postings_for_shadow(income_shadow)`
  for the deleted payment, then (after the delete) `sync_loan_payment_postings(loan, scenario)` to
  re-split the downstream payments whose running balance changed. (Wired at `:690` only -- NOT the
  payback paths; a loan payment is never a credit payback.)
- **True-up.** In `anchor_service.apply_loan_anchor_true_up:309`, after the anchor add/flush, loop
  `for sid in _scenarios_with_loan_payments(account.id): sync_loan_payment_postings(account.id, sid)`.
- **Rate change.** Same scenario-loop sync in `routes/loan/escrow_rates.py:107` AND
  `routes/loan/params.py:196`, after the row mutation, before commit.
- **Loan params first created (N1).** Same scenario-loop sync at the end of `routes/loan/params.py`
  create, so payments settled before the loan was resolvable get their corrections.

Idempotent (reconcile-to-target) and self-healing; a missed hook repairs at the next sync and the
completeness oracle catches gaps on real data. The loan sync touches only loan / interest / escrow /
refund ledgers (never Checking). Idempotency rests on single-transaction reconcile-to-target; the
global true-up / rate paths have no optimistic lock (M4) -- safe for a solo user; an advisory lock on
the loan is the hardening if concurrency ever matters.

---

## 8. The reconciliation oracle (`tests/test_integration/test_posting_ledger_loan_reconciliation.py`)

Correctness rests on **hand-computed literals on synthetic fixtures with distinct off-schedule amounts**
plus a **parallel run** against the resolver (which must AGREE on on-schedule data and DIVERGE exactly
as predicted off-schedule).

1. **Hand-computed split (the core).** Synthetic loans with distinct per-payment amounts and a known
   rate path: assert each correction's `interest` / `escrow` and the loan net (`principal`) equal
   hand-computed literals. Cases: on-schedule; **+extra principal**; **short payment** (negative
   principal, balances); **payoff overpayment -> Refund leg**; an ARM rate step (interest changes
   mid-history); a no-escrow loan (escrow leg dropped).
2. **Parallel run vs resolver.** On an **on-schedule** fixture and on the **real Mortgage**, assert
   `anchor - sum(real principal postings) == resolve_account_loan(...).current_balance` (they must
   match). On an **off-schedule** fixture, assert they DIVERGE and the ledger equals the hand-computed
   real balance (proving the ledger is the more-correct one).
3. **Completeness over the full set.** Every post-anchor settled loan payment <= as_of (including
   post-payoff, routed to Refund) has a correction; no Step-2 cash entry on a loan ledger is left
   uncorrected. Future-dated settled payments (none today) are asserted absent and flagged (a
   `log`/skip with an explicit message), not silently passed.
4. **Per-entry balance** (`SUM = 0`, `COUNT >= 2`) and **trial balance** (global `SUM = 0`).
5. **The CRITICAL-bug regression.** After a correction exists, revert/delete the payment and assert the
   **full** cash reversal posts (Checking ledger returns to 0; `_posted_net` unaffected by the
   correction).
6. **Lifecycle.** settle -> posts; revert -> reverses; restore -> re-posts; **hard delete -> nothing
   stranded** (reverse-before-delete); **true-up past a posted payment -> reverses it + re-splits
   downstream, Checking untouched**; **base-rate AND escrow-rate change -> re-splits**; **multi-scenario
   true-up syncs every scenario**; **loan-params-created-after-settle -> back-posts** (N1).
7. **Superseding the cash oracle for loans.** Replace the per-account `account_posting_total(loan) ==
   settled_transfer_effect + settled_transaction_effect` assertion (which breaks once corrections exist)
   with the loan-aware invariant: `account_posting_total(loan) == settled_transfer_effect(loan) -
   sum(interest + escrow + excess corrections)` `== anchor - current_balance`.
8. **Scenario + owner isolation; backfill == go-forward.**
9. **Two adversarial non-vacuity proofs:** tamper a payment's `actual_amount` -> a hand-computed split
   check FAILS; inject an unbalanced leg -> the trial balance CATCHES it.

---

## 9. Atomic commits

Each independently green (targeted tests + `pylint app/` 10.00 on touched files) with an adversarial
`code-reviewer` pass on the staged diff before committing. Migrations tested up and down. Full suite is
the final gate in the last commit (run alone).

1. **DONE (`4d5d0ff`). Ref:** `principal`/`interest`/`escrow`/`refund` posting kinds, `loan_payment`
   source, and the `ledger_account_kinds` ref table + enums + seeds + inline-seed migration. Tests:
   `ref_cache` resolution; `init()` succeeds; up/down; deploy `init_ref_cache=False` path holds.
   (`equity_opening` deferred to the Section-10 read switch, so every seeded kind has a live consumer.)
2. **DONE (`c26a899`). Schema:** `ledger_accounts.kind_id` (3-step backfill from the NULL-pattern) +
   `loan_account_id` (RESTRICT) + partial unique + the **columns-only** shape CHECK (D14 -- not the
   `kind_id IN (...)` form originally drafted here); rewrote the model docstring taxonomy around the
   explicit kind; the sole writer (`ledger_account_service`) stamps `linked`/`category`/`fallback`.
   Tests: backfill maps every existing row to the right kind (via the shape-CASE evaluated as a SELECT,
   since `kind_id` is NOT NULL at HEAD); partial unique rejects a duplicate (loan, kind); shape CHECK
   rejects a malformed loan row; up/down (pinned constraint names). **As-built gotcha:** making `kind_id`
   NOT NULL broke re-running two *shipped* migrations' backfill SQL in tests (b82538084d24 linked,
   7d63529e4300 category/fallback) -- their frozen INSERTs omit `kind_id`, the migrations are not
   editable, and `ON CONFLICT DO NOTHING` does not rescue the NOT NULL; fixed by injecting the kind the
   Step-4 backfill would assign into the frozen SQL (shared `inject_cash_backfill_kind_id` helper for
   7d63). Verified up/down/up on the prod-clone dev DB (9 linked + 15 category backfilled correctly,
   `flask db check` no drift, both FKs RESTRICT); full suite 6668; `pylint app/ scripts/` 10.00;
   code-reviewer clean.
3. **DONE (`c3937ce`). Chart resolver:** `get_or_create_loan_ledger_account(user_id, loan_account_id,
   kind)` (lazy, idempotent by the partial unique, flush-no-commit) + the `_LOAN_LEDGER_KINDS` map +
   `_find_existing_loan_ledger_account` + `_load_amortizing_loan_account`; mirrors
   `get_or_create_category_ledger_account`; class derived from the kind (interest/escrow -> Expense,
   refund -> Asset); snapshot name clipped to the column width. **Two LOAD-BEARING guards** stand in for
   the columns-only shape CHECK (D14): the kind must be a loan kind, AND the account must classify
   AMORTIZING (D15 -- the developer-ratified addition beyond the original class/kind scope; closes the
   gap the CHECK leaves on `loan_account_id`). Tenancy load by `(id, user_id)`, deliberately NOT
   `is_active`-filtered (an archived loan with settled history still resolves). Pure write-only -- no
   migration, no wiring, no read change. Tests (+18 in `test_ledger_account_service.py`): shape / class /
   name per kind; idempotent; three kinds one loan; two loans independent; archived-loan-resolves;
   rejects a non-loan kind (x4) and a non-amortizing account (x4: Checking / HYSA / Credit Card /
   Brokerage); missing / foreign loan (tenancy); name truncation. (The shape CHECK and per-(loan, kind)
   unique are covered by `test_models/test_ledger_account.py::TestLoanLedgerShapeAndUnique`.) Verified:
   `pylint app/ scripts/` 10.00 (no `duplicate-code` despite the structural mirroring); full suite 6686;
   code-reviewer clean (its one LOW -- the archived-loan coverage gap -- fixed and proven non-vacuous by
   mutating the `is_active` filter in, which fails the test).
4. **DONE (`f4992d0`). The split + service (pure, no wiring):** `compute_loan_payment_splits(loan_id,
   scenario_id, as_of)`, `sync_loan_payment_postings(loan_id, scenario_id, as_of)`,
   `reverse_loan_payment_postings_for_shadow(income_shadow)` -- in a NEW
   `app/services/loan_posting_service.py` (sibling to `posting_service`, which would exceed the 1000-line
   module gate; reuses its shared `_emit_balanced_entry` / `_PostingLeg` / `_ledger_account_for` /
   `_civil_settle_date` / `_MAX_DESCRIPTION_LENGTH` via a pylint-clean cross-module import). **As-built
   deviations:** (a) the reader is `_posted_loan_payment_legs(transaction_id) -> {ledger_id: (net,
   posting_kind_id)}`, richer than the drafted `_posted_loan_payment_net` -- it carries each ledger's
   kind so a component reversed to zero (e.g. escrow removed) reverses with the kind it was posted under;
   one-kind-per-ledger holds, and any violation fails LOUD at the balanced-write gate rather than
   silently under-reversing. (b) `sync_loan_payment_postings` takes an explicit `as_of` (the wiring in C5
   passes `date.today()`; consistent with `resolve_account_loan`), not the 2-arg sketch. (c) escrow is
   `calculate_monthly_escrow(components)` -- **no inflation** (developer decision; see the header
   deviation 4 and D3). (d) the monthly-interest accrual was unified into
   `app/utils/money.accrue_monthly_interest` (4 inline copies -> 1: `_replay_payment_row`,
   `_amortize_forward`, the deleted `_period_interest`, and the split) so the split's interest is
   byte-identical to the resolver's by construction -- this was a `code-reviewer` MEDIUM (the formula the
   parallel-run premise rests on must be shared, not a hand-asserted copy). (e) enabling DRY extractions:
   `is_confirmed_payment_eligible` (shared by `replay_schedule` and the split walk); exposed
   `loan_resolver.select_latest_anchor` / `resolve_periods`; `loan_payment_service.load_rate_changes` /
   `load_loan_params` / `load_anchor_events` / `load_active_escrow_components`. **Whole-loan
   reconcile-to-target** plus `_stale_loan_payment_shadows` (reverses any posted-but-no-longer-eligible
   shadow -- reverted / un-settled / re-anchored; hard-deletes excluded by the SET-NULL `transaction_id`,
   handled by reverse-before-delete in C5). Tests (24 in `test_loan_posting_service.py`): hand-computed
   splits (on-schedule, +extra, short/negative, escrow, payoff->Refund, post-payoff all-Refund, ARM step,
   actual-drives-split [non-vacuity], pre-anchor/projected excluded, no-LoanParams guard) + sync posts /
   idempotent / multi-payment / no-escrow-drops-leg / payoff-Refund / never-touches-Checking /
   transfer_id-invisible (the CRITICAL regression, via an independent replica of `_posted_net`) + reverse
   zeroes / no-op / unsettle-reverses + anchor-move re-splits-survivors-and-reverses-pre-anchor +
   disjoint-from-the-Step-3-path. Verified: real-Mortgage split reproduces 1018.82 / 616.99 / 275.14
   exactly (read-only, dev DB pristine); `pylint app/ scripts/` 10.00; full suite **6710**; code-reviewer
   **no CRITICAL/HIGH** (M1 accrual-unification + L1 docstring-refs + L3 escrow-loader + L5 two
   hardening-tests all fixed; M2 biweekly-redistribution divergence + L4 `paid_at`-ORM-read documented as
   notes for C5 / the read switch, not defects). **Read-switch note (M2):** the split walks raw shadows
   without the resolver's `_redistribute_to_distinct_months`, so the C7 / read-switch oracle must treat a
   biweekly-collision-at-a-rate-boundary loan as an expected divergence (the ledger is the more-correct
   record), not assert exact ledger==resolver equality.
5. **DONE (`45fa001`; original Commit 5, T3). Lifecycle wiring** (preceded by the temporal-escrow
   prerequisite T1/T2): the C4 split service is wired at every chokepoint that changes a loan's confirmed
   payments. **As-built sites (re-anchored to live code):** `transfer_service.update_transfer` (inside the
   `_POSTING_RELEVANT_FIELDS` block, AFTER the cash sync) and `restore_transfer` (after the cash sync)
   call `_sync_loan_payment_postings_if_loan`; `delete_transfer` calls `_reverse_loan_payment_before_delete`
   (loads the income shadow WITHOUT the `is_deleted` filter, so a hard delete of an already-soft-deleted
   transfer still reverses) BEFORE the delete and captures the loan / scenario ids, then
   `_resync_loan_payment_postings_after_delete` AFTER (the soft/hard branches were restructured into an
   if/else with a shared tail); `anchor_service.apply_loan_anchor_true_up`,
   `routes/loan/escrow_rates.add_rate_change`, and `routes/loan/params.update_params` (UNCONDITIONAL --
   `payment_day` also moves `is_confirmed_payment_eligible`) call the all-scenarios sync; `create_params`
   back-posts the N1 case. **As-built deviations:** (a) the four transfer-service wiring helpers live in a
   NEW `app/services/_transfer_loan_posting.py` sibling (the 1000-line module gate, mirroring
   `_transfer_ownership.py`); (b) the true-up + both rate paths share ONE
   `loan_posting_service.sync_all_scenarios_or_duplicate` helper (flush-not-commit) -- the DRY resolution
   of a `duplicate-code` gate finding, NOT the per-site inline try/except drafted here; the anchor / rate
   duplicate-idempotency holds because the helper's first query autoflushes the pending row, so the
   collision surfaces inside its one `try`. **Tests** (`tests/test_integration/test_loan_posting_wiring.py`,
   15): Section 8.5 full-cash-reversal regression on revert + hard delete, 8.6 lifecycle (settle / revert /
   soft+hard delete / downstream-resplit / restore / true-up-Checking-untouched / multi-scenario /
   duplicate-same-day-idempotent / ARM-rate / base-rate / `payment_day`-eligibility / N1), non-loan-ignored;
   the actual-amount case uses a payoff-overpayment so the Refund leg is genuinely cash-dependent (a
   non-vacuity fix from the `code-reviewer` pass). Shared loan-posting test helpers extracted into
   `_test_helpers.py`; the C4 suite delegated to them (one C4 test's premise changed, so it now drives a
   projected shadow). Verified: `pylint app/ scripts/` 10.00; full suite **6739**; `code-reviewer` CLEAN
   (no CRITICAL/HIGH/MED; 3 LOW all addressed). Pre-existing out-of-scope note: an unused `anchor_service`
   import in `tests/test_services/test_anchor_service.py` (not gated -- CI lints only `app/`).
6. **DONE (as-built, T4; developer-ratified Option A, 2026-07-01). Historical backfill
   (production-wide):** one correction per confirmed post-anchor settled loan payment, real-split,
   per-loan accounts; **idempotent via reconcile-to-target** (stronger than the drafted `NOT EXISTS` --
   a payment already carrying a go-forward correction is at target, so nothing is re-posted).
   **Design fork the plan's raw-SQL sketch could not hold:** unlike the Step-2/3 cash effects (a
   one-line SQL formula), the loan split is a running-balance walk over rate periods + effective-dated
   escrow, so it CANNOT be reproduced in raw SQL without duplicating the money-critical split engine
   (the drift the unified `accrue_monthly_interest` exists to prevent). It must reuse the go-forward
   split service, which is built on `ref_cache` + the service layer -- but the migration host runs
   `create_app(init_ref_cache=False)` (the `3104f87` fix) and the `db239773c2fd` policy forbids
   importing the service into a migration. **Resolution:** the backfill runs in the POST-migration
   deploy hook `scripts/init_database.py::backfill_loan_payment_postings_after_migration` (inits
   `ref_cache`, then calls the idempotent app-layer
   `loan_posting_service.backfill_all_loan_payment_postings`, which loops
   `sync_loan_payment_postings_all_scenarios` over every loan via the new
   `loan_payment_service.load_all_loan_account_ids` -- so backfill == go-forward by construction). A
   thin boundary migration `e2a9f1c7b4d6` (new head, down_revision `d1e7c4a2f9b3`) has a documented
   NO-OP upgrade and owns only the reversible raw-SQL teardown (downgrade deletes `source_kind =
   loan_payment` entries + per-loan `loan_account_id IS NOT NULL` ledger accounts), required so the
   Commit-2 (`efca4315bf81`) schema downgrade is clean -- exactly the ordering its docstring depends on.
   Tests (`tests/test_integration/test_loan_posting_backfill.py`, 14): posts-a-missing-correction
   (hand-computed 500/500; escrow 3-leg; multi-payment running balance 497.50), no-double-post +
   idempotent, all-loans/owners/scenarios coverage, non-loan-untouched + no-payment-no-op, the
   deploy-hook commit contract (proven non-vacuous via a separate connection AND a mutation test that
   drops the commit), migration revision-pair + downgrade-removes-loan-data-keeps-Step-2 + source guard.
   Executable Alembic downgrade/upgrade round-trip verified on the rebuilt template. `pylint app/
   scripts/` 10.00; `code-reviewer` no Crit/High (M1 hook-commit coverage + L2 stale-idle-transaction
   hardening + L3 dead-disable all fixed). The exact prod-clone outcome (posts the one Mortgage
   correction 275.14 / 1018.82 / 616.99, nothing for the Van) is the T5 manual step (§9.7); this step
   leaves the dev DB pristine for it.
7. **DONE (as-built, T5; see the T5 paragraph in the header for detail). The reconciliation oracle**
   (`tests/test_integration/test_posting_ledger_loan_reconciliation.py`, 12 tests -- Section 8):
   parallel run vs the resolver (agree on-schedule, diverge off-schedule = the ledger proven the
   more-correct record; 8.2), the loan-aware superseding invariant driven where the cash per-account one
   breaks (8.7), a rich interest+escrow+refund fixture (8.1/8.3/8.4), scenario/owner isolation of the
   whole sweep (8.8), backfill == go-forward reconciles identically (8.8), two non-vacuity proofs (8.9),
   plus a developer-ratified pre-anchor boundary test pinning the Section-10 "pre-anchor cleanup" gap.
   Non-vacuity proven by a `+$10` interest-bug injection (9 of 11 tests failed, incl. all parallel-run
   tests). Full suite **6765 passed** (run alone; baseline 6753 + 12); `pylint app/ scripts/` 10.00; NO
   migration added (the oracle is a test file), so no template rebuild. Docs: this plan + Step 4 marked
   done (write-only) in `level1_level2_scope_and_fitness.md` + the Step-4 memory.
   **Remaining (developer-gated): Manual (prod-clone dev, 2FA off):** the backfill posted the Mortgage
   correction + nothing for the Van; mark the next Mortgage payment Paid -> new correction + reconcile;
   mark it with a +$500 actual -> the ledger shows principal 775.14 while the resolver still shows the
   contractual balance (proving the ledger is the more-correct record awaiting the read switch); revert
   -> full cash reversal (CRITICAL regression). Leave the dev DB pristine (re-clone if needed). Then the
   `dev -> main` PR so CI runs.

---

## 10. The next step (read switch -- explicitly out of scope here)

Once this foundation is proven, the immediate follow-up plan switches confirmed loan reads onto the
ledger and ends the true-up dependency:

- **Opening-balance posting** per loan (the latest anchor as an `equity_opening` entry: `Loan +balance /
  Equity -balance`), so `loan balance = sum(postings)` is self-sufficient (uses the reserved
  `equity_opening` kind from Section 4.1).
- **Switch the seam's AMORTIZING confirmed read** to `sum(loan-ledger postings up to T)`, with the
  resolver projecting forward from the ledger's current confirmed balance (projections stay derived,
  Option D). The boundary rule for pre-first-point periods stays in the seam (Level 1).
- **Retire the resolver's confirmed replay** (its job is now the ledger's); pre-anchor cleanup;
  true-ups become rare genuine corrections, not per-off-schedule-payment band-aids.
- Re-evaluate the escrow-as-asset model (D8) and the statement-split override (D13) as their own
  features if wanted.

Gated by the cross-page equality oracle + the loan oracle (Section 8), validate-then-switch.

---

## 11. Out of scope

The opening-balance / Equity posting and the read switch (Section 10); pre-anchor cleanup; escrow as a
tracked impound asset; the statement-split override; investment / property postings; single-entry
consolidation; any change to underlying payment quirks (Rule 6).

---

## 12. Risks and rollback

- **The loan sync must never touch Checking** -- the correction posts only loan/interest/escrow/refund;
  the true-up regression and the CRITICAL regression both assert Checking unchanged.
- **The `transaction_id` linkage is load-bearing** -- the CRITICAL regression (8.5) proves `_posted_net`
  is unaffected; a `code-reviewer` pass confirms no `transfer_id` is set on a correction.
- **Real-split correctness** -- hand-computed oracle fixtures (8.1) + the parallel run (8.2); no engine
  change, so all existing resolver/amortization numeric tests stay byte-for-byte green.
- **Running-balance coupling** -- the whole-loan re-sync on every chokepoint + the downstream re-sync on
  delete; the lifecycle regressions (8.6) cover true-up / rate / delete re-splits.
- **Completeness** -- the full-set oracle (8.3) including post-payoff Refund; future-dated settled is
  flagged, not dropped.
- **Concurrency on global triggers (M4)** -- single-transaction reconcile-to-target; advisory lock if
  ever needed.
- **Rollback.** Each commit independently revertible; reads unaffected throughout (the ledger is
  parallel until Section 10). Reverting the schema commit (2) requires reverting the backfill (6) first.

---

## 13. Definition of Done

1. All commits landed, each green and each with its `code-reviewer` pass applied.
2. `pylint app/ scripts/` 10.00 with every `--fail-on` checker; zero new messages.
3. Full suite passes (count shown), run alone.
4. All migrations tested upgrade AND downgrade; `build_test_template.py` rebuilt.
5. The loan reconciliation oracle is green, production-wide, and non-tautological (hand-computed +
   parallel-run + off-schedule + the CRITICAL regression).
6. Docs + memory updated; manual prod-clone verification done (including the +$500-actual demonstration
   that the ledger is the more-correct record).
7. Developer asked before commit/push; `dev -> main` PR opened so CI runs.

---

## Appendix -- citation index

Resolver ignores cash: `rate_period_engine.py:553,581,597-599`; `loan_resolver/_periods.py:346-350`.
Cash-ledger corruption (resolved by D7): `posting_service.py:208-237,630,733`. Architecture goal:
`level1_level2_scope_and_fitness.md:496-497,523,580`. Escrow stored: `loan_features.py:116-156`;
`escrow_calculator.py:137-177,180-196`; DB monthly escrow = 616.99. Shadows / discovery:
`transfer_service.py:217-258,389-397`; `loan_payment_service.py:215-259`. Chokepoints:
`transfer_service.py:80,641,690,714,951`; `anchor_service.py:309,386`; `routes/loan/escrow_rates.py:107`;
`routes/loan/params.py:97,112,196`. Finalised-edit lock: `transfers/mutations.py:61-98`;
`state_machine.py:236-288`. `journal_entries.transaction_id`: `journal_entry.py:206-214`.
Immutability / balanced trigger: `journal_entry.py:370-423`; `posting_infrastructure.py:82-124`.
Ledger taxonomy (being replaced by D9): `ledger_account.py:12-67`. Read path on resolver:
`balance_at.py:491-516`; `net_worth_kernel.py:182`; `loan_payment_service.py:535-547`. Payback never a
transfer: `credit_workflow.py:305-306,529-540`. No version_id (M4): `loan_anchor_event.py`,
`loan_features.py`. Real data: prod-clone dev DB, 2026-06-30 (103/206 ledger; anchors 177,829.83 /
15,663.59; 1 post-anchor Mortgage / 0 Van; zero divergent actuals; configured escrow 616.99).
