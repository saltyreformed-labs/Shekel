> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Implementation plan: effective-dated (temporal) escrow, and the correct loan-payment split

**Status:** IN PROGRESS (2026-07-01). **T1 SHIPPED** to the feature branch (`ac409b5`, as-built);
**T2 SHIPPED** (this commit). T3-T5 pending (Section 7). Prerequisite that emerged while wiring
Build-Order Step 4 (`implementation_plan_posting_ledger_loan_payments.md`, Commit 5). Three developer
decisions (2026-07-01) reshaped that commit: (1) fix the escrow-immutability defect NOW rather than
defer it to the read switch; (2) fix it the fully-normalized way -- effective-date the escrow config --
rather than a per-payment snapshot; (3) model the effective-dating as `[effective_date, end_date)`
range columns on `EscrowComponent` rather than new schedule tables. This doc records that design and the
revised commit sequence; the Step-4 plan's Commits 5-7 now sit AFTER the temporal-escrow prerequisite
below (they become T3-T5).

**Branch:** `feat/posting-ledger-loan-payments` (same branch; temporal escrow is a prerequisite of the
correct split, so it lands as earlier commits in the same Step-4 PR).
**Migration head at planning time:** `efca4315bf81` (verified 2026-07-01).

---

## 1. The defect (plain language)

A confirmed loan payment's real split is `principal = cash - interest - escrow`. The Step-4 split
(`loan_posting_service.compute_loan_payment_splits`, shipped `f4992d0`) computes `escrow` from the
loan's **current** escrow config (`calculate_monthly_escrow(active_components)`) and applies that one
number to **every** past payment on each recompute. That is correct only while escrow never changes.

Escrow components carry no effective-date history, so the moment the user raises or adds one, the next
sync re-splits **past** payments with the new, higher escrow -- overstating their escrow and
understating the principal they actually paid.

Worked example. June mortgage: escrow $616.99, cash $1,910.95 -> principal **$275.14** (posted). Add a
$600/yr insurance line, so monthly escrow -> $666.99. The next sync (e.g. settling July) rewrites June
to escrow $666.99, principal **$225.14** -- claiming June paid $50 less principal than it did, drifting
the loan balance up $50.

It is latent today (real data: one stable $616.99 component, one post-anchor payment; write-only step,
nothing reads the ledger). It bites when escrow changes over time AND the read switch goes live.

## 2. Why on-schedule needs escrow to cancel (the load-bearing invariant)

On-schedule, `cash = scheduled_pi + escrow_built`. The split's principal is
`cash - interest - escrow_split = scheduled_pi + escrow_built - interest - escrow_split`. For this to
equal the scheduled principal (`scheduled_pi - interest`), we need `escrow_split == escrow_built`: the
escrow used in the split MUST equal the escrow embedded in that payment's cash.

**As-built finding (T2): only the split changes; the cash builder is already as-of-correct.** The
recurring-transfer cash builder (`payment_transfer._resolve_transfer_amount`) and the live projection
recompute (`loan_payment_service.live_loan_transfer_amounts` -> `load_loan_context` ->
`load_active_escrow_components`) build cash for FUTURE/projected payments at the CURRENT escrow. Because
no escrow version can be future-dated (add stamps `effective_date = today`; delete stamps `end_date =
today`), "escrow as of any future date" IS the current escrow -- so current-escrow cash IS as-of-correct
for the dates it projects, and a `derive_from_loan` payment's cash tracks the current escrow right up to
settle, when it freezes. The split reads only HISTORICAL (settled) payments and keys escrow by each
payment's pay-period start (the same date its rate is keyed by), so `escrow_split` equals the escrow the
frozen cash was built with, and the cancellation holds without touching the cash path. The two would
diverge only if an escrow change fell in the ~2-week window between a payment's period start and its
settle date (escrow changes are ~annual), an accepted narrow edge.

## 3. The model: effective-dated component ranges

`budget.escrow_components` gains two columns and loses `is_active`:

- `effective_date DATE NOT NULL` -- when this version of the component takes effect.
- `end_date DATE NULL` -- when it stops (exclusive); NULL = still in effect.
- **Active on date D** iff `effective_date <= D AND (end_date IS NULL OR D < end_date)`.
- **Currently active** = `end_date IS NULL`. This replaces `is_active` (which was exactly this concept:
  "not removed"), so keeping both would be redundant state -- `is_active` is dropped, the range is the
  single source of truth. Mirrors the app's existing `RateHistory` effective-dating pattern.

Editing maps onto ranges through the existing add/delete flow (there is no amount-edit route today):
- **Add** a component -> insert a row `effective_date = today, end_date = NULL`.
- **Remove** a component -> `end_date = today` on the active row (was `is_active = False`).
- **Change** an amount -> close the old row (`end_date = today`) + insert a new one -- i.e. delete +
  add, which the UI already expresses.

Constraints:
- `uq_escrow_components_account_name_active` -- partial unique `(account_id, name) WHERE end_date IS
  NULL` (at most one *active* version per name; historical versions may repeat the name). Replaces the
  total `uq_escrow_account_name`.
- `ck_escrow_components_date_range` -- `end_date IS NULL OR end_date >= effective_date` (`>=` admits a
  same-day add-then-delete zero-length "never active" range).
- `ix_escrow_components_account_effective` -- `(account_id, effective_date, end_date)`, serving the
  split's per-loan `load_all_escrow_components` (the `account_id` prefix) and any future as-of query.

## 4. Escrow as of a date, and inflation

- **As-built (T2):** rather than a per-date query, the split loads EVERY version once
  (`load_all_escrow_components(account_id)`) and filters each payment's date in memory with
  `EscrowComponent.is_active_on(D)` (`effective_date <= D < end_date`) -- one query for the whole walk,
  not the N+1 a per-date `escrow_components_as_of` query would have been.
- `calculate_monthly_escrow(components)` sums the recorded `annual_amount / 12` of whatever set it is
  given (the `is_active` gate is gone -- the caller passes the right set). It returns **recorded**
  amounts, so a past date always yields the same figure: immutable by construction.
- **Inflation** (the old `as_of_date` compounding) is demoted to a **forward-projection** concern only:
  recorded past/present escrow is exact; a surface that projects escrow into the future may still
  compound the latest recorded value forward. The loan split walks only historical payments
  (`as_of = today`), so it uses recorded amounts with no inflation -- matching the C4 deviation-4
  decision exactly.

## 5. Backfill (a no-op on current data)

Existing rows are backfilled so behaviour is byte-identical to today:
- `effective_date` = the loan's `origination_date` (from `LoanParams`; a floor at or before every
  payment date), so every historical payment sees today's escrow exactly as it does now.
- `end_date` = NULL for currently-active (`is_active = TRUE`) rows.
- Already-inactive (`is_active = FALSE`) rows: their real removal date was never recorded, so it cannot
  be reconstructed. Best-effort `end_date = updated_at::date` (the last mutation, i.e. the
  deactivation), `effective_date = LEAST(origination_date, updated_at::date)`. **Documented
  limitation:** a past payment that predates such a row's real deactivation may see a slightly wrong
  escrow. On real data there are no inactive components, so this is vacuous today; it is called out so
  the read switch's oracle treats a historical-inactive-escrow loan as an expected (best-effort)
  divergence rather than a bug.

So on current data every payment sees the current $616.99 and temporal escrow changes nothing; only a
*future* escrow change ever creates a second range.

## 6. Surfaces touched (traced 2026-07-01)

`calculate_monthly_escrow`: `loan_posting_service` (the split -> **as-of**), `loan_payment_service.
load_loan_context` (current -> today's set), `escrow_rates` add/delete OOB (current), `loan/dashboard`
next_year/portion (current + forward inflation), `savings_dashboard_service._metrics` (current).
`calculate_total_payment` (cash): `loan/payment_transfer.py`, `loan/dashboard.py:413`,
`loan/_helpers.py:276` -- all **UNCHANGED**; current escrow is as-of-correct for the future payments the
cash projects (Section 2 as-built finding). Direct `EscrowComponent`/`is_active` queries: `escrow_rates`
add/delete/list, `loan_payment_service.load_active_escrow_components`, `savings_dashboard_service._data`,
`accounts/crud` delete-all (unaffected). All "current" surfaces move from `is_active = TRUE` to
`end_date IS NULL` (equivalent post-backfill -> no behaviour change); only the split reads the as-of
escrow (T2).

## 7. Revised commit sequence

Temporal-escrow prerequisite, then the Step-4 Commits 5-7:

- **T1 -- schema. DONE (`ac409b5`).** The columns, drop `is_active`, constraints, index, migration
  `d1e7c4a2f9b3` (3-step effective_date backfill + end_date backfill + drop column, working downgrade
  verified up/down), model (drop `IsActiveMixin`, add columns + `is_active_on` predicate), the
  add/delete routes (open/close ranges), and the current-escrow read consumers (`is_active` ->
  `end_date IS NULL`, behaviour-preserving). Destructive (drops `is_active`) -> `Review:` line in the
  migration docstring. As-built refinements from testing/review: range CHECK is `>=` (a same-day
  add-then-delete zero-length range is valid); `effective_date` default is a CALL-TIME
  `_default_effective_date` (so it respects `freeze_today` and matches the app-clock `end_date`);
  `build_escrow_display` no longer self-filters (aligned with `calculate_monthly_escrow`, so
  rows-sum-to-badge holds for any input). Tests: migration up/down + backfill derivations, range CHECK,
  partial unique, add-opens / delete-closes. Full suite 6723; pylint 10.00; code-reviewer clean.
- **T2 -- correct split via as-of escrow. DONE (this commit).** `EscrowComponent.is_active_on(d)` (the in-memory range
  predicate) + `load_all_escrow_components` (active + removed versions); the split sums each payment's
  escrow over the versions in effect on its pay-period start, so it is immutable for a past date. The
  cash builder is UNCHANGED -- current escrow is already as-of-correct for the future payments it
  projects (Section 2 as-built finding). Tests: hand-computed escrow-change-over-time (distinct escrow
  per date; both splits frozen when a later version is added); the 24 existing C4 split tests stay green
  with the escrow fixture effective-dated to origination.
- **T3 (Step-4 C5) -- lifecycle wiring** into the six chokepoints.
- **T4 (Step-4 C6) -- historical backfill** of corrections.
- **T5 (Step-4 C7) -- reconciliation oracle** + full suite + docs + manual prod-clone verification.

Each commit independently green (targeted tests + `pylint app/ scripts/` 10.00), with a `code-reviewer`
pass on the staged diff and migrations tested up/down. Full suite is the final gate in T5.

## 8. Out of scope

Escrow as a tracked impound asset; the statement-split override; any read-path change (the read switch
is the Step-4 Section-10 follow-up); reworking inflation beyond demoting it to forward-projection.
