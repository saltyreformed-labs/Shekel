> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Carry-Forward Aftermath -- Alternative Options (Code-Grounded)

## Context

Commit `33cd21e` relaxed the partial unique index on
`(template_id, pay_period_id, scenario_id)` (and the parallel transfer
index) so that `is_override=TRUE` rows can coexist with the
rule-generated row. This fixed a real production bug (`Carry Fwd` 500
when a template-linked row collided with an existing canonical) but
shipped an unintended side effect: every read of "transactions in a
period" can now show two rows for the same template-period.

`docs/carry_forward_aftermath_design.md` proposes a 5-phase envelope
display layer (Option D) at ~910 LoC. The user has rejected Option D
because it produces a permanent cell-vs-subtotal divergence ($135
envelope display vs $200 period subtotal vs $200 balance reduction).
**Hard requirement: any acceptable solution must produce identical
totals across cell, period subtotal, and balance projection.**

This document explores alternatives to D that satisfy the hard
requirement, derived from reading the actual code rather than the
design doc's option list.

---

## Key code facts that constrain the design

These are the load-bearing facts I confirmed before brainstorming:

1. **`TransactionTemplate.track_individual_purchases`**
   (`app/models/transaction_template.py:47`) is an existing boolean
   that already distinguishes envelope-style templates (groceries,
   spending money, gas -- True) from discrete-obligation templates
   (rent, subscriptions -- False). The disambiguator the user needs
   already exists in the schema. No new column required.

2. **`Transaction.effective_amount`** (`app/models/transaction.py:145-169`)
   returns `Decimal("0")` when `status.excludes_from_balance` is True
   (Credit, Cancelled). Otherwise prefers `actual_amount` over
   `estimated_amount`. This is the single source of truth used by
   subtotal AND balance calc.

3. **Balance calculator includes only Projected**
   (`app/services/balance_calculator.py:406-419`). Settled (Done /
   Received / Settled / Paid) rows are excluded -- their cash flow is
   assumed already reflected in the anchor balance. **This is the
   crucial insight**: once you settle a row at `actual_amount = X`,
   it stops contributing to forward subtotals and balance projections
   automatically. No display-layer math needed.

4. **Period subtotal** (`app/routes/grid.py:263-282`) sums Projected
   transactions only, using `effective_amount`. Same filter as
   balance calc, so they always agree.

5. **Existing settle path with auto-compute from entries** lives at
   `app/routes/transactions.py:288-370` (`mark_done`). For tracked
   templates it computes `actual_amount` from entry sums. The pattern
   for "settle this tracked row at its real spend" already exists.

6. **Recurrence skip rules**
   (`app/services/recurrence_engine.py:102-128`): skip if any existing
   row in the period has `is_immutable`, `is_override=True`,
   `is_deleted=True`, or any entries. A bumped canonical (with
   `is_override=True`) blocks future re-generation -- correct, we want
   the bump preserved.

7. **`is_override` write paths today** -- only two:
   - `app/routes/transactions.py:274` -- manual amount/period edit
     bumps the flag.
   - `app/services/carry_forward_service.py:107` -- carry-forward sets
     it.

## The realization

Option D's divergence is not inherent to envelope semantics. It is
inherent to **keeping two rows in the same period**. If the data
model only ever holds one row per (template, period) for envelope
items, every consumer reads the same number naturally. **The fix
belongs in the data layer, not the display layer.**

The user's actual intent on `Carry Fwd` differs by template kind:

- **Tracked templates**: "settle source at the real spend; roll the
  unspent leftover into next period." This is envelope rollover.
- **Untracked templates**: "I forgot to mark this paid; defer the
  whole obligation to next period." This is what 33cd21e enabled.

These are not the same operation. Forking carry-forward by
`track_individual_purchases` is the cleanest correct behavior, not a
DRY violation.

---

## Options that preserve subtotal == cell == balance

### Option F (recommended): Tracked-flag-driven settle-and-roll

When `Carry Fwd` runs, branch each Projected source row by template
flag:

- **`template.track_individual_purchases = True`** (envelope):
  1. Compute `entries_sum = sum of source.entries`
  2. Settle source: `status_id = DONE` (or `RECEIVED` for income),
     `actual_amount = entries_sum`, `paid_at = now`.
  3. Compute `leftover = max(Decimal("0"), source.estimated_amount -
     entries_sum)`.
  4. If `leftover > 0`, find the target period's canonical row for
     the same `(template_id, scenario_id, is_deleted=False,
     is_override=False)`. Bump
     `target_canonical.estimated_amount += leftover` and set
     `target_canonical.is_override = True`.
  5. If target canonical does not yet exist (recurrence has not run),
     run the per-template recurrence path to generate it, then bump.
     Alternative: skip the bump and let the next recurrence pass do
     it (would need a small queue / pending-bump field; only worth
     adding if (5) actually occurs in practice).
  6. No sibling row is ever created. The unique index from 33cd21e
     is not exercised on this path.

- **`template.track_individual_purchases = False`** (discrete) and
  ad-hoc rows (no template_id) and shadow transfers:
  - Use the existing 33cd21e behavior unchanged: move the row whole,
    set `is_override = True` if template-linked.

#### Why this satisfies the hard requirement

For the worked example (wife: $100 spending money, $65 spent in A,
$0 in B):

| View | Source A | Target B | Total |
|------|----------|----------|-------|
| Period subtotal (sums Projected via effective_amount) | excludes settled source | $135 (bumped canonical) | $135 across A+B |
| Balance projection (sums Projected) | $0 (settled = excluded) | reduces by $135 | reduces by $135 forward |
| Cell display | "$65 / $100, Done" badge | "$0 / $135 Projected" | identical to subtotal |
| Anchor balance | already reflects the $65 in checking | -- | -- |

Every consumer sees the same numbers. There is one row per envelope
per period. No display-layer aggregation is necessary. Dashboard
upcoming bills, budget variance, year-end summary, companion view --
all work without modification.

The $65 is not lost: it lives on the settled source row's
`actual_amount` and on its TransactionEntry rows, contributing to
historical reports.

#### Edge cases handled

- **Wife overspent ($120 entries, $100 estimate)**: settle at
  actual=$120, leftover = max(0, 100-120) = 0, no bump. Target
  unchanged. Correct.
- **Wife spent zero**: settle at actual=$0, leftover = $100, target
  canonical bumps from $100 to $200. Subtotals shift cleanly: source
  -$100 (settled), target +$100 (bumped), net same forward
  obligation.
- **Multi-hop (carry-forward two periods running)**: each hop
  re-applies the same logic. Source A settles, target B's canonical
  bumps. Next period, source B settles (including the bump), target
  C's canonical bumps. Naturally chains. No provenance column
  needed.
- **Target canonical missing**: see step (5) above.

#### Effort

- `app/services/carry_forward_service.py` -- add envelope branch:
  ~60 LoC.
- New helper `_settle_source_and_roll(txn, target_period_id)`: ~40
  LoC. Could share code with the existing `mark_done` route's
  settle logic by extracting a service helper -- ~20 LoC of refactor
  in `app/routes/transactions.py`.
- Tests for envelope path + edge cases (zero entries, overspend,
  missing canonical, multi-hop, untracked still works): ~150 LoC.
- Migration: none.
- Schema: none.
- Models: none.
- Templates: none.
- Routes: none (one shared helper extracted, optional).

**Total: ~250 LoC mini-sprint.** Reversibility: high (code-only,
no schema). Production cleanup of any sibling pairs already created
under 33cd21e: a one-time script that, for each existing pair,
settles the source-position one and folds its leftover into the
canonical (~50 LoC).

#### What stays from 33cd21e

The relaxed unique index and the `no_autoflush` fix stay. They are
still required for the **untracked / discrete** carry-forward path
(rent, utilities, etc.). 33cd21e was not a wrong fix -- it was an
incomplete fix that treated every template the same.

---

### Option G: Settle-and-roll into a separate ad-hoc rollover row

Same as F, but instead of bumping the canonical's `estimated_amount`,
create a new ad-hoc row in the target period (no `template_id`,
name = `"<template name> rollover"`, `estimated_amount = leftover`,
`is_override = True`).

- **Pros**: clearer audit trail (the rollover is a distinct row that
  reads "carried from period X").
- **Cons**: doubles row count for envelope categories. Wife sees two
  rows in the target period -- "Spending Money $100" and "Spending
  Money rollover $35". Mental-model fit is worse than Option F. The
  user's stated mental model is ONE envelope per period.
- **Subtotals still match**: subtotal sums both rows = $135. Balance
  calc same.
- **Effort**: similar to F (~250 LoC), maybe +30 LoC for naming
  conventions.

Listed for completeness; F is preferred.

---

### Option H: Manual per-row "Settle and Roll" button (no auto-detect)

Reserve `Carry Fwd` exclusively for discrete deferral (current
33cd21e behavior). Add a per-row "Settle & Roll" affordance on past-
period rows -- visible only when `template.track_individual_purchases
= True`.

- **Pros**: explicit user intent per-row; no auto-detect risk if a
  template is misclassified.
- **Cons**: more clicks for the bulk case (the user clicked Carry
  Fwd to handle everything at once). Adds UI surface.
- **Subtotals**: same as F (one-row-per-period).
- **Effort**: ~150 LoC (button + route + service helper + tests).

Worth considering if you want the user to opt-in per-row rather than
trust the template flag.

---

### Option I: Cleanup-only, no carry-forward semantic change

Roll the wife's actual workflow back to "edit next period's amount"
for envelope items, and leave `Carry Fwd` as it stands post-33cd21e.

- **Action**: zero code. Add a one-line UI hint near `Carry Fwd`:
  "Tracked items: edit next period's amount instead." Optionally,
  hide `Carry Fwd` for tracked templates entirely (~20 LoC) so the
  user cannot create the doubled-row condition.
- **Subtotals**: trivially match because no rollover rows are ever
  created.
- **Cons**: relies on user discipline / UI guidance. Anyone who does
  click Carry Fwd on a tracked template still creates the doubled-row
  condition. Doesn't actually solve the original problem (wife's
  unspent envelope rolling forward) -- requires manual amount editing
  in the next period instead.

Listed for completeness. Acceptable if the user prefers no automation
of the rollover.

---

## Comparison

| Option | Subtotals match? | Schema change? | LoC | UX fit | Reversibility |
|--------|------------------|----------------|-----|--------|----------------|
| D (design doc) | **No** -- $135 vs $200 | +1 column | 910 | one cell per envelope (with divergence note) | medium |
| **F (this doc)** | **Yes** | **none** | **~250** | **one row per envelope** | **high** |
| G | Yes | none | ~280 | two rows per envelope (canonical + rollover) | high |
| H | Yes | none | ~150 | per-row manual button | high |
| I | Yes (if discipline holds) | none | 0-20 | "don't use Carry Fwd for envelopes" | trivial |

## Recommendation

**Option F.** The user's hard constraint (subtotals must match)
eliminates Option D and the design doc's premise of a display-layer
fix. Once the constraint is honored, the answer is to keep one row
per (template, period) for envelope items, which means settling source
on `Carry Fwd` and folding the leftover into target's canonical. The
disambiguator (`track_individual_purchases`) already exists. The math
falls out of the existing `effective_amount` and balance-calculator
filters automatically -- no new helpers, no aggregation layer, no DOM
id rekeying, no migration.

This is a mini-sprint, not a phase. ~250 LoC, three or four focused
commits (service branch + helper extraction + tests + production
cleanup script). 33cd21e remains correct for discrete obligations
(rent, utilities); the envelope path is purely additive.

## Critical files for an Option F implementation

- `app/services/carry_forward_service.py` -- add envelope branch
  inside the `no_autoflush` block; call new helper.
- `app/routes/transactions.py:288-370` -- consider extracting the
  "settle a tracked txn from entries" logic into a service helper
  (`app/services/transaction_service.py` or similar) so the
  carry-forward path and the manual mark-done path share one
  implementation.
- `app/models/transaction_template.py:47` -- read-only; verify
  `track_individual_purchases` is the right flag (it is).
- `app/services/recurrence_engine.py` -- read-only; confirm bumped
  canonical (is_override=True) blocks regeneration (it does).
- `app/services/balance_calculator.py` -- read-only; confirm settled
  Done rows are excluded (they are, lines 406-419).
- `tests/test_services/test_carry_forward_service.py` -- add envelope-
  path tests covering: tracked + entries < estimate (rollover),
  tracked + entries > estimate (no rollover), tracked + zero entries
  (full rollover), tracked + missing target canonical, tracked +
  multi-hop, untracked still moves whole, ad-hoc still moves whole,
  shadow transfers still take existing path.

## Verification

End-to-end test of the worked example:

1. Create a tracked template ($100 spending money, biweekly).
2. Let recurrence generate rows in periods A, B, C.
3. Add $65 of entries against A's row.
4. Click `Carry Fwd` on A.
5. Assert: A's row is Done with `actual_amount = 65`. B's canonical
   row has `estimated_amount = 135`, `is_override = True`. No new
   rows created. Period B subtotal increases by $35 (from the bump).
   Balance calc reduces forward projection by an additional $35 in
   B (offset by A no longer contributing $100). Net forward cash
   flow unchanged across A+B.
6. Repeat for an untracked template (rent): assert row moved whole,
   sibling exists in target, current 33cd21e behavior preserved.

## Open question for the user

`track_individual_purchases` is the natural disambiguator, but the
flag was added for a different feature (entry tracking UI). Is it
acceptable to overload it as the envelope-rollover signal, or should
we add a separate `is_envelope` flag at the same time? Recommendation:
overload it. Templates the user marks as tracked are the ones the
wife uses entries on, which is exactly the envelope set. A separate
flag would almost certainly be set in lockstep with the existing one.
