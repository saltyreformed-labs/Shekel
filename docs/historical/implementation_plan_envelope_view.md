> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Combined Envelope View for Carry-Forward Siblings -- Plan

## Context

The carry-forward bug fix is shipped (commit `33cd21e`, merged to main as
`95da8c5`). Production now correctly creates override-sibling pairs in the
target period when the user carries an unpaid template-linked row forward.

The remaining UX problem: those two rows render as **two stacked cells** in
the grid (e.g. `12/400` and `65/400` for groceries). For envelope-style
budget items the wife maintains (groceries, spending money, gas), this is
hard to read -- the wife has to mentally sum two progress counters to know
how much envelope is left.

The user wants the grid to display these two rows as one combined envelope.
Worked example:

> Wife has `$100` spending money per period. In period 4/9 she spent `$65`,
> leaving `$35` unspent. User clicks Carry Fwd. In period 4/23 she gets
> another `$100`. After spending `$18` in 4/23 she should see one envelope
> reading `18/135` -- `$18` spent of `$135` available (`$100` new + `$35`
> rolled forward), `$117` remaining.

## Constraints driving the design

1. **Maintain financial correctness.** No data merging. Two underlying
   transaction rows remain in the database for audit, status independence,
   and per-row payment workflows.
2. **Match the envelope mental model.** Display `entries_spent_in_current_period
   / available_envelope`, not `sum_of_entries / sum_of_estimates`.
3. **DRY / SOLID.** One aggregation helper, used by desktop grid, mobile
   grid, and companion view. No fork between envelope and discrete items.
4. **Companion access preserved.** Wife's entry-recording flow stays
   intact; new entries always route to the canonical row so they count
   toward envelope spent.

## Why a schema column is required

`is_override=True` alone cannot distinguish "carried from a prior period"
from "manually edited in this period." Both currently set the flag (`app/
routes/transactions.py:273-274` for manual edits; `app/services/
carry_forward_service.py:107` for carry-forward). The envelope math needs
those two cases handled differently:

- **Carried row:** entries on it represent prior-period spend. Must NOT
  count toward `envelope_spent` for the new period.
- **Manually-edited canonical row:** entries on it ARE this period's spend.
  Must count toward `envelope_spent`.

Adding a nullable FK column `carried_from_period_id` resolves this cleanly.

## Recommended approach: envelope view-model + `carried_from_period_id`

### 1. Schema migration

New Alembic migration adding `carried_from_period_id INT NULL` to both
`budget.transactions` and `budget.transfers`:

- FK reference: `budget.pay_periods.id` with `ON DELETE SET NULL`
- Partial index `idx_<table>_carried_from_period` WHERE
  `carried_from_period_id IS NOT NULL` (rare backward queries)
- Up-migration: add column, add FK, add index
- Down-migration: drop in reverse

No data backfill. Existing rows leave the column NULL (none of them were
carried, so this is correct).

### 2. Model definitions

- `app/models/transaction.py` -- add the column (nullable Integer FK with
  `ondelete="SET NULL"`); add backref/relationship to `PayPeriod` if
  reasonable (probably not -- pay_periods rarely look back).
- `app/models/transfer.py` -- same.

### 3. Carry-forward service writes the column

`app/services/carry_forward_service.py` -- in the existing
`db.session.no_autoflush` block (lines 104-109), set
`txn.carried_from_period_id = source_period_id` alongside the
`is_override` and `pay_period_id` updates.

For shadow transfers, extend `transfer_service.update_transfer` to accept
`carried_from_period_id` as a kwarg (parallel to the existing `is_override`
block at `transfer_service.py:535-540`); propagate to xfer + both shadows.
Update the docstring at `transfer_service.py:427-441`. Update the
`carry_forward_service.py:121-126` call to pass the kwarg.

### 4. Recurrence engine skip-condition update

**Critical correctness fix.** `app/services/recurrence_engine.py:107-128`
currently treats any `is_override=True` row as a "skip this period" signal.
With envelope semantics, a carried row should NOT block canonical
generation -- otherwise the user gets a single-member envelope (all
carried) where they wanted a 2-member envelope (canonical + carried).

Change the skip condition: skip only if a non-carried override exists.
Concretely: `existing_txn.is_override AND existing_txn.carried_from_period_id
is None` triggers skip; carried rows alone do not.

Mirror the same change in `app/services/transfer_recurrence.py:78-91`.

This is a deliberate semantic change to the recurrence engine. Tested
explicitly.

### 5. Display-layer aggregation helper

New module `app/services/grid_aggregation.py` exporting:

```python
@dataclass(frozen=True)
class EnvelopeCell:
    members: tuple[Transaction, ...]      # all matched siblings, ordered
    canonical: Transaction | None         # the carried_from_period_id IS NULL row
    carried: tuple[Transaction, ...]      # carried_from_period_id IS NOT NULL rows
    envelope_budget: Decimal              # see math below
    envelope_spent: Decimal               # see math below
    primary_member_id: int                # for click default + entry routing
    has_carried: bool                     # drives badge UX (deferred)
    summary_status_id: int                # canonical's status if present, else first carried's

def build_envelope_cells(
    period_txns: list[Transaction],
    entry_sums: dict[int, dict],
) -> dict[tuple[int, int | str], EnvelopeCell]:
    """Group period_txns by (category_id, template_id or name) and
    return one EnvelopeCell per group.

    envelope_budget = canonical.estimated_amount (or 0 if no canonical)
                      + sum(max(0, c.estimated_amount - entry_sums[c.id].total)
                            for c in carried)

    envelope_spent  = entry_sums[canonical.id].total (or 0 if no canonical)

    Single-member groups (the common case) produce an EnvelopeCell with
    members=(txn,), canonical=txn, carried=(), envelope_budget=
    txn.estimated_amount, envelope_spent=entry_sums[txn.id].total.  This
    keeps the cell partial uniform.

    For settled canonicals (status.is_settled), envelope_spent falls
    back to canonical.actual_amount or canonical.effective_amount so
    the display remains consistent after the wife marks the envelope
    Paid.
    """
```

Called from `app/routes/grid.py:index` once per period after
`txn_by_period` is built. Result threaded into the template alongside (or
in place of) the raw matched lists.

This helper is **pure** (no DB access; reads already-loaded objects).
Easily unit-testable.

### 6. Cell partial -- single uniform input

`app/templates/grid/_transaction_cell.html` -- refactor to take an
`EnvelopeCell` instead of (or in addition to) a single `Transaction`.

- For `len(members) == 1`: render exactly as today (no visual change).
- For `len(members) > 1`: render combined progress
  `envelope_spent / envelope_budget`, plus a small "carry-forward" indicator
  (the deferred badge UX) and a member count if useful.
- Status badge: use `summary_status_id` (canonical's status when present).
- Override indicator (pencil icon): show when canonical is_override (manual
  edit) OR there are carried members.
- Click handler: `hx-get` defaults to canonical's quick-edit. When canonical
  is None (rare; only if carry-forward beats recurrence and the engine
  skip-condition fix isn't in effect), fall back to the first carried
  row's quick-edit.

### 7. Grid template loops

`app/templates/grid/grid.html` -- the existing matching loops at lines
150-176 (income) and 226-249 (expense) build a `matched` list per
(period, row_key) and include the cell partial once per matched txn.
Replace with: build the `EnvelopeCell` for each (period, row_key) (using
the helper output already in template context), include the cell partial
once per envelope.

`app/templates/grid/_mobile_grid.html` -- mirror the same change in the
duplicated matching loops.

`app/templates/companion/index.html` -- companion currently iterates
`{% for txn in transactions %}` and renders one card per txn. Refactor:
group by (template_id) using the helper (or a parallel companion-side
helper), render one envelope card per group.

### 8. Partial-swap target IDs

This is the second critical correctness fix. Today, after a quick-edit
save, `app/routes/transactions.py:_render_cell_response` (lines 50-67)
re-renders a single-txn cell into `#txn-cell-{id}`. Three more handlers
in `app/routes/transfers.py` (lines 657, 764, 804) do the same for
transfer cells. Under envelope semantics, that single-txn swap leaves the
envelope display stale until next full page load.

Fix: change the cell wrapper DOM ID from `#txn-cell-{txn.id}` to a
stable per-envelope ID like `#envelope-cell-{period_id}-{template_id}`
(or `-{name}` for ad-hoc rows). The four route handlers need to:

1. Identify the envelope the saved txn belongs to (period + template OR
   period + name).
2. Reload that envelope's siblings.
3. Build a fresh `EnvelopeCell` view-model.
4. Render the cell partial with the envelope.
5. Swap into the envelope-level DOM ID.

This is a non-trivial ripple but is the correct shape -- the cell IS
the envelope after this change. The existing `_render_cell_response`
helper is the right place to centralize the new logic.

### 9. Entry-recording flow

`app/routes/entries.py` -- no changes to the route. The cell partial
sets the form's `txn_id` to `envelope.primary_member_id` (canonical's ID
when present). Existing `_get_accessible_transaction` checks remain
per-txn, which is correct.

`app/templates/grid/_transaction_entries.html` -- entries form already
takes a single `txn`; threading is via the existing `txn_id` URL param.
Cell partial passes canonical's id when opening the popover.

Edge case: companion-visibility on canonical. If a template is set
`companion_visible=False` AFTER a carry-forward already happened, the
carried row stays visible to the companion (because companion_service's
join is on the current template state, not the historical state).
Routing entries to canonical preserves the existing access rules.

### 10. Period subtotals and balance calculator

**No change.** `app/routes/grid.py:266-282` (subtotals) and
`app/services/balance_calculator.py` continue to sum every non-deleted,
non-excluded transaction. The husband's period total reflects full
cash-flow obligation ($200 in our example, $100 + $100); the wife's
envelope cell shows $135 (envelope-experiential). Documented divergence:

- Cell display: per-period envelope view (wife's experiential).
- Period subtotal: per-period cash-flow obligation (husband's
  planning view).
- Balance projection: per-period cash-flow obligation (financial truth).

Reasoning: routing envelope semantics into balance_calculator would
distort projected balances (carried obligations are real money you
still owe until the carried row is settled). Best to keep the balance
math at cash-flow truth and document the cell-vs-subtotal divergence
in the cell tooltip ("$35 carried from prior period -- adds to envelope
but not to period total until paid").

### 11. UX badge (the previously deferred item)

Folded into this plan as a free side-effect: with `carried_from_period_id`
populated, the cell partial can show a `bi-arrow-right-circle` icon
on envelopes with `has_carried=True`, with a tooltip "Carried forward
from <period date range>." No additional schema work; just template
markup.

### 12. Tests

- **Migration up/down** -- as before.
- **carry_forward_service** -- new assertion that
  `carried_from_period_id == source_period_id` after move; null on
  rule-generated rows. Same for transfers.
- **transfer_service** -- new tests for the `carried_from_period_id`
  kwarg propagating to xfer + both shadows.
- **recurrence_engine + transfer_recurrence** -- explicit tests for the
  skip-condition change: carried row alone does NOT skip generation;
  non-carried override still skips.
- **grid_aggregation** -- unit tests for envelope_budget, envelope_spent
  under all canonical/carried combinations including:
  - 1 canonical, 0 carried (single member)
  - 1 canonical, 1 carried (the user's `18/135` example)
  - 1 canonical, 2 carried (multi-hop)
  - 0 canonical, 1 carried (case b)
  - settled canonical (uses actual_amount fallback)
  - all rows cancelled / deleted (no envelope returned)
- **Cell rendering** -- snapshot/HTML assertion that combined cells
  render the right numbers; single-member cells unchanged.
- **Quick-edit save** -- after editing canonical, the swap target
  rebuilds the envelope, not just the canonical row.
- **Companion view** -- envelope card shows combined progress; entries
  default to canonical.

Existing carry-forward, recurrence, balance-calculator, and grid tests
should continue to pass with no edits except where the recurrence skip
rule changes (one or two recurrence tests need updating; flagged
during implementation).

## Files to change

| File | Change |
|------|--------|
| `migrations/versions/<new>.py` | New: add `carried_from_period_id` to transactions and transfers, with FK, index, and downgrade |
| `app/models/transaction.py` | Add `carried_from_period_id` column |
| `app/models/transfer.py` | Add `carried_from_period_id` column |
| `app/services/carry_forward_service.py` | Set the column during the no_autoflush move; pass via update_transfer for shadows |
| `app/services/transfer_service.py` (lines ~427-540) | Accept `carried_from_period_id` kwarg; propagate to xfer + both shadows |
| `app/services/recurrence_engine.py` (line 114) | Update skip condition: skip only on non-carried overrides |
| `app/services/transfer_recurrence.py` (line 83) | Mirror skip-condition change |
| `app/services/grid_aggregation.py` | New: `EnvelopeCell` dataclass + `build_envelope_cells` helper |
| `app/routes/grid.py` (function `index`) | Call helper after txn_by_period; thread envelopes into template context |
| `app/templates/grid/grid.html` | Replace per-txn loop with per-envelope loop |
| `app/templates/grid/_mobile_grid.html` | Mirror per-envelope change |
| `app/templates/grid/_transaction_cell.html` | Take EnvelopeCell input; render combined display when multi-member; show carry-forward badge when has_carried |
| `app/templates/companion/index.html` | Group transactions into envelopes |
| `app/templates/companion/_transaction_card.html` | Render envelope card |
| `app/routes/transactions.py` (lines 50-67) | `_render_cell_response` rebuilds envelope on save; swaps into envelope-level DOM id |
| `app/routes/transfers.py` (lines 657, 764, 804) | Same envelope-rebuild logic |
| `tests/test_services/test_carry_forward_service.py` | Add carried_from_period_id assertions |
| `tests/test_services/test_recurrence_engine.py` | Update + add skip-condition tests |
| `tests/test_services/test_grid_aggregation.py` | New: full coverage of envelope math edge cases |
| `tests/test_services/test_companion_service.py` | Envelope view + canonical-routed entries |
| `tests/test_routes/test_grid.py` (or grid_regression) | Snapshot test for envelope cell HTML |
| `tests/test_routes/test_transactions.py` | Quick-edit save rebuilds envelope swap |

## Verification

1. **Migration up/down** -- `flask db upgrade`, `flask db downgrade
   c79bfaef598e`, `flask db upgrade`. No constraint violations; column
   appears and disappears cleanly.
2. **Targeted unit tests** -- `pytest tests/test_services/
   test_grid_aggregation.py tests/test_services/test_carry_forward_service.py
   tests/test_services/test_recurrence_engine.py
   tests/test_services/test_transfer_service.py
   tests/test_services/test_companion_service.py -v --tb=short`.
3. **Targeted route tests** -- `pytest tests/test_routes/test_grid_regression.py
   tests/test_routes/test_transactions.py
   tests/test_routes/test_transfers.py -v --tb=short`.
4. **End-to-end repro on dev DB** -- recreate the user's example:
   - Set up a `companion_visible=True` "spending money" template at $100.
   - Period N: rule-generated row, record $65 of entries.
   - Carry Fwd to period N+1.
   - Verify cell shows `0/135` (or similar).
   - Add $18 entry against the canonical row.
   - Verify cell shows `18/135`.
   - Verify period N+1's cash-flow subtotal still shows $200 obligation
     (or whatever the test data implies).
5. **Companion login (manual)** -- as the linked companion, navigate to
   the period after carry-forward; confirm one envelope card with combined
   progress; record an entry; confirm it lands on canonical and the
   envelope total updates.
6. **Quick-edit re-render** -- click a multi-member envelope, edit the
   canonical row's amount, save; confirm the envelope cell rebuilds
   correctly (not just the canonical row swap).
7. **Full test suite gate** -- split per directory:
   - `pytest tests/test_services/ -v --tb=short`
   - `pytest tests/test_routes/ -v --tb=short`
   - `pytest tests/test_integration/ tests/test_models/ tests/test_schemas/
      tests/test_utils/ tests/test_concurrent/ tests/test_adversarial/
      tests/test_audit_fixes.py -v --tb=short`
   - `pytest tests/test_scripts/ tests/test_performance/ -v --tb=short`
8. **Pylint** -- `pylint app/ --fail-on=E,F`.

## Out of scope

- Threading envelope semantics into `balance_calculator` and dashboard
  totals. Documented divergence stands; revisit if user feedback says
  the cell-vs-subtotal mismatch is confusing.
- Pre-existing double-counting of carried rows in
  `dashboard_service.py:236`, `year_end_summary_service.py`, and
  `budget_variance_service.py`. These already over-count under the
  current override-sibling model; not introduced by this change.
  Track separately if desired.
- Two-hop carry-forward provenance preservation. Current design
  overwrites `carried_from_period_id` on each hop ("most recent
  source"). If full chain history is needed, a separate audit table
  can be added later.
- A second "envelope subtotal" row alongside the cash-flow subtotal.
  Could be added in a follow-up if the cell-vs-subtotal divergence
  proves confusing.

## Effort and risk

Roughly 900-1100 LoC across schema, services, route handlers, templates,
and tests. Risk medium overall, dominated by:

- Recurrence-engine skip-rule change (correctness-critical; tested
  explicitly).
- DOM ID change for envelope-level partial swaps (touches 4 route
  handlers and 3 templates).
- Companion view refactor (currently iterates per-txn; needs the
  envelope grouping).

The schema column itself is low risk -- nullable, no backfill, isolated.

## Open decisions made (with reasoning)

1. **Recurrence skip rule:** updated to ignore carried-only overrides.
   Required for case (b) to work. Tested explicitly; a couple of
   existing recurrence tests may need updating to reflect the
   correctly-narrowed skip semantics.
2. **Click target when canonical is None:** open the first carried
   row's quick-edit. Simplest fallback; rare in practice once the
   recurrence skip rule fix is in.
3. **Subtotal philosophy:** keep at cash-flow truth ($200), add a
   tooltip on the cell explaining the rolled-forward portion. Do not
   thread envelope semantics into balance_calculator.
4. **Two-hop carry-forward:** overwrite `carried_from_period_id` on
   each hop. Simple. Add an audit chain later if needed.
5. **Settled canonical math:** when canonical's status is settled,
   `envelope_spent` falls back to `actual_amount`/`effective_amount`
   instead of `entries_sum`. Matches existing single-cell behavior.
