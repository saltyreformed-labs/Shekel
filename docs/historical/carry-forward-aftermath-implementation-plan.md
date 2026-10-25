> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Carry-Forward Aftermath -- Implementation Plan (Decisions Resolved)

## Context

Commit `33cd21e` relaxed the partial unique index on
`(template_id, pay_period_id, scenario_id)` so that `is_override=TRUE`
rows can coexist with the rule-generated row. This fixed a real
production 500 (envelope `Carry Fwd` collided with an existing
canonical) but shipped an unintended side effect: every read of
"transactions in a period" can now show two rows for the same
template-period -- a "doubled-row" condition that breaks the user's
mental model and forced manual cleanup of one pair in production
(wife's spending money envelope; one row already settled).

The original design doc (`docs/carry_forward_aftermath_design.md`)
proposed a 5-phase, ~910 LoC display-layer fix (Option D). That was
rejected because it produces a permanent cell-vs-subtotal divergence.

`docs/carry-forward-aftermath-design.md` (Option F) proposed instead a
~250 LoC data-layer fix that branches `Carry Fwd` by template kind:
envelope templates settle source + roll the leftover into the target
canonical; discrete templates keep the post-`33cd21e` move-whole
behavior. The user has resolved every open decision in that doc. This
plan records those decisions, fills the gaps the original surfaced as
"open questions," and lists the concrete files to change.

**Hard requirement (unchanged):** any acceptable solution must produce
identical totals across the cell display, the period subtotal, and
the balance projection. Option F satisfies this naturally because it
maintains one row per (template, period) for envelope items, so
`effective_amount` (single source of truth at
`app/models/transaction.py:145-169`) and the balance calculator's
Projected-only filter (`app/services/balance_calculator.py:406-419`)
both see the same number.

---

## Resolved Decisions

| Key | Decision | Notes |
|-----|----------|-------|
| **A** | Missing target canonical | **Reuse `generate_for_template`** to create the canonical, then bump. Same outcome as the original "Option 2" (synthesize), but achieved via the engine's existing public API rather than hand-construction. Avoids field-init drift if recurrence ever changes. |
| **B** | Settled target canonical | **Refuse with validation error.** Rare scenario (carrying into an already-reconciled period). Better than silent-loss-of-leftover or surprise ad-hoc rows. |
| **C** | Income envelope templates | **Reject `is_envelope=True` on income templates at Marshmallow level.** Late-deposit and uncleared-check scenarios are status/period concerns, handled cleanly by the existing discrete carry-forward path and the `Received -> Settled` workflow. They do not need envelope semantics. (DB-level enforcement: see "Schema enforcement note" below.) |
| **D** | Transfers | **No envelope semantics.** `TransferTemplate` has no `track_individual_purchases` column. The envelope branch must guard `transfer_id IS NULL` so shadow transfers stay on the existing 33cd21e path. |
| **E** | Migration backfill | **Identity rename.** Alembic `op.alter_column(..., new_column_name=...)`; existing data preserved one-for-one. |
| **F** | Production cleanup | **Manual.** Only one sibling pair exists in production; one row already marked Paid. The user will clean up by hand via the UI. No script. |
| **G** | Shared settle helper | **Extract.** New module `app/services/transaction_service.py` exposes `settle_from_entries(txn, *, paid_at=None) -> None`. Used by both the existing `mark_done` route and the new envelope branch in `carry_forward_service`. |
| **H** | Carry-forward UI | **Pre-flight confirmation modal.** New `GET` endpoint returns a populated modal listing per-row planned actions (settle-and-roll, defer, or refuse). Confirm button posts to the existing carry-forward endpoint. Whole batch refuses if any row would refuse (atomic). |

---

## Architecture (one-paragraph summary)

`Carry Fwd` becomes a two-step UX: click button -> modal preview
(`GET /pay-periods/<id>/carry-forward-preview`) -> confirm posts to
the existing `POST /pay-periods/<id>/carry-forward`. Inside the
service, source rows partition by `transfer_id IS NOT NULL`
(transfers stay on the existing path) and then by
`template.is_envelope` (envelope rows settle source + bump target
canonical via shared helper; non-envelope rows take the existing
move-whole path). Shared helper `settle_from_entries` extracted from
`mark_done`. `is_envelope` is a renamed-from-`track_individual_purchases`
column with Marshmallow-level enforcement that it is False on income
templates.

---

## Schema enforcement note (Decision C)

The original design doc proposed "Marshmallow + DB CHECK constraint"
for income+envelope rejection. However, PostgreSQL `CHECK` constraints
cannot reference columns in other tables, and "is income" lives on
`ref.transaction_types`, not on `budget.transaction_templates`.

Three ways to honor a DB-level rule:

1. Denormalize `is_income` onto `transaction_templates` and add
   `CHECK(NOT (is_envelope AND is_income))`. Cost: +1 column, must be
   kept in sync with `ref.transaction_types` (trigger or app code).
2. `BEFORE INSERT/UPDATE` trigger that joins to `ref.transaction_types`
   and raises. Cost: triggers are rare in this codebase; adds a layer
   developers must remember to inspect.
3. **Marshmallow `@validates_schema` only** (chosen). The flag is set
   only on template create/update, both of which already route through
   Marshmallow. Cost: trust the input boundary; document the gap.

This plan picks **option 3** as the pragmatic baseline. The flag is
written rarely (template form submissions only), the validation runs
on every such submission, and `ref_cache.transaction_type_is_income(id)`
makes the lookup trivial. If the user prefers a denormalized column +
hard CHECK constraint, that can be added without touching the
carry-forward logic.

---

## Phased implementation

Each phase is independently testable. Run targeted tests after each
phase; full suite once at the end as the final gate.

### Phase 1 -- Rename `track_individual_purchases` -> `is_envelope`

- Alembic migration: `op.alter_column("transaction_templates",
  "track_individual_purchases", new_column_name="is_envelope")`. Real
  downgrade (rename back). No data movement.
- Update every reference (verified inventory):
  - `app/models/transaction_template.py:47-49`
  - `app/schemas/validation.py:96` (TemplateCreateSchema field name)
  - `app/routes/transactions.py:250-258` (Credit-status guard)
  - `app/routes/transactions.py:350` (mark_done condition)
  - `app/routes/templates.py:55-56` and `:335` (form/getattr lists)
  - `app/services/credit_workflow.py:63`
  - `app/services/entry_service.py:142`
  - `app/services/dashboard_service.py:225`
  - `app/templates/grid/_transaction_full_edit.html:95, 135`
  - `app/templates/templates/list.html:80, 163`
  - `app/templates/templates/form.html:92-94` (input id, name, label)
  - `app/templates/companion/_transaction_card.html:9`
  - Existing migration `b961beb0edf6_add_entry_tracking_and_companion_support.py`
    is left untouched -- the new migration handles the rename.
- Targeted tests: rerun
  `pytest tests/test_routes/test_templates.py tests/test_services/test_entry_service.py
  tests/test_services/test_credit_workflow.py tests/test_services/test_dashboard_service.py
  tests/test_routes/test_transactions.py -v --tb=short`
  to confirm nothing references the old name.

### Phase 2 -- Marshmallow validation: reject `is_envelope=True` on income templates (Decision C)

- `app/schemas/validation.py`:
  - Add `@validates_schema` to `TemplateCreateSchema` (and inherited
    by `TemplateUpdateSchema`) that calls
    `ref_cache.transaction_type_is_income(transaction_type_id)`. If
    `is_envelope` is True and the type is income, raise a
    `ValidationError` with a message like
    `"is_envelope is only valid on expense templates"`.
  - Confirm `ref_cache` exposes (or add) a small accessor for the
    income/expense flag on a transaction type. If `ref_cache` already
    caches `transaction_types`, this is a one-line addition.
- Tests in `tests/test_routes/test_templates.py`:
  - POST template with income type + `is_envelope=True` -> 400.
  - POST template with expense type + `is_envelope=True` -> 200.
  - PATCH template flipping `is_envelope` to True on income type -> 400.

### Phase 3 -- Extract shared `settle_from_entries` helper (Decision G)

- Create `app/services/transaction_service.py`:
  ```python
  def settle_from_entries(txn, *, paid_at=None):
      """Settle a tracked transaction at sum(entries).

      Sets actual_amount = sum(entries), status to DONE (expense) or
      RECEIVED (income), paid_at = now if not provided. Caller is
      responsible for the surrounding session/commit lifecycle.

      Pre: txn.template.is_envelope is True, txn has a status that is
      mutable (Projected or carry-target equivalent), txn.transfer_id
      is None (transfers settle through transfer_service).
      """
  ```
  - Reuses `compute_actual_from_entries` from `app/services/entry_service.py:398-416`.
  - Mirrors the income/expense status branch from
    `app/routes/transactions.py:306-309`.
- Refactor `mark_done` (`app/routes/transactions.py:288-370`) to call
  `transaction_service.settle_from_entries(txn)` for the
  `template.is_envelope and entries` branch. Keep the manual
  `actual_amount` form-override path untouched.
- New tests `tests/test_services/test_transaction_service.py`:
  - Expense + entries -> status DONE, actual_amount = sum, paid_at set.
  - Income + entries -> status RECEIVED, actual_amount = sum.
  - Zero entries -> actual_amount = 0 (do not fall back to estimated).
  - Overspend (entries > estimated) -> actual_amount = entries sum
    (no clamping; reflects truth).
- Existing `mark_done` tests must still pass without modification.

### Phase 4 -- Carry-forward envelope branch (the core change)

- `app/services/carry_forward_service.py`:
  - Inside the existing `no_autoflush` block, branch each
    non-transfer source row by `template is not None and
    template.is_envelope`.
  - Envelope branch (per row):
    1. Compute `entries_sum` (call `compute_actual_from_entries`).
    2. Look up the target canonical:
       `Transaction` filtered by `(template_id, pay_period_id=target,
       scenario_id, is_deleted=False, is_override=False)`. There
       should be at most one (the rule-generated row).
    3. **If target canonical exists and is settled** (status is one
       of DONE / RECEIVED / SETTLED / PAID -- check via the
       `excludes_from_balance` flag is wrong here; check explicitly
       against the mutable-status set since SETTLED rows still live
       in balance history): raise a `ValidationError` with a clear
       message naming the source row and target period. Whole batch
       fails because we are inside a single transaction. (Decision B,
       atomic per Decision H confirmation.)
    4. **If target canonical does not exist:** call
       `recurrence_engine.generate_for_template(txn.template,
       [target_period], scenario_id)`. The engine will create the
       canonical (no existing row blocks it). Re-query to obtain the
       new row. (Decision A.)
    5. Compute `leftover = max(Decimal("0"),
       source.estimated_amount - entries_sum)`.
    6. If `leftover > 0`, increment
       `target_canonical.estimated_amount` by `leftover` and set
       `target_canonical.is_override = True`.
    7. Settle source via
       `transaction_service.settle_from_entries(source)`.
       Source's `pay_period_id` does NOT change -- the source row
       stays in its original period as a settled record.
  - Discrete branch: unchanged from the current 33cd21e behavior
    (regular non-envelope template-linked rows and ad-hoc rows
    relocate via `pay_period_id = target` + `is_override = True`).
  - Transfers (`transfer_id IS NOT NULL`): unchanged. Confirm with an
    explicit `if txn.transfer_id is not None` guard at the top of the
    branching logic.
- Tests in `tests/test_services/test_carry_forward_service.py`
  (extend, do not rewrite -- the file already has discrete-path
  coverage):
  - Envelope, partial spend (entries < estimate): source DONE at
    entries_sum, target canonical bumped by leftover, target
    `is_override=True`, target row count unchanged.
  - Envelope, zero entries: source DONE at 0, target bumped by full
    estimate.
  - Envelope, overspend (entries > estimate): source DONE at
    entries_sum, leftover = 0, target unchanged.
  - Envelope, missing target canonical: engine creates it via
    `generate_for_template`, then it bumps. Recurrence skip rules
    don't fire because the row didn't exist.
  - Envelope, target canonical settled: raises ValidationError; no
    state changes (transaction rolled back). Verify with a follow-up
    `db.session.rollback()` assertion or session expiry check.
  - Envelope, multi-hop (carry A->B then B->C in two calls): chains
    cleanly. Source A DONE; B's canonical bumped; later, source B
    (now the bumped canonical, status PROJECTED) DONE; C's canonical
    bumped.
  - Income template with `is_envelope=False` (the only valid income
    case post-Phase-2): takes the discrete path. (Phase 2 makes
    `is_envelope=True` on income unreachable.)
  - Mixed batch: 1 envelope expense + 1 discrete expense + 1 income
    + 1 shadow transfer. Verify each took the correct path.
- Read-only verification on
  `app/services/recurrence_engine.py:102-128`: skip-on-override
  blocks regeneration of a bumped canonical, so the next run of
  recurrence engine will not overwrite the bump. (Already correct;
  noting for the test plan.)

### Phase 5 -- Confirmation modal + preview endpoint (Decision H)

- New route `GET /pay-periods/<int:period_id>/carry-forward-preview`
  in `app/routes/transactions.py`:
  - Compute the same partition the service would (transfers /
    envelope / discrete) and per-envelope-row plan: `settle at $X,
    roll $Y forward` or `BLOCKED: target settled`.
  - Render
    `app/templates/grid/_carry_forward_preview_modal.html`
    (new file). Bootstrap 5 modal partial. Lists each row with its
    planned action and totals at the bottom: "N envelopes settle and
    roll, M discrete defer, P transfers move." Confirm button issues
    `hx-post` to the existing carry-forward route.
  - If any row is BLOCKED, the Confirm button is `disabled` and the
    modal shows the blocking row prominently with a hint
    ("Resolve manually then re-open the modal.").
- Update `app/templates/grid/grid.html:110-119`:
  - Replace the direct `hx-post` form with a button that does
    `hx-get="{{ url_for('transactions.carry_forward_preview',
    period_id=period.id) }}"` and `hx-target="#confirm-modal-mount"
    hx-swap="innerHTML"`. Add a single `<div id="confirm-modal-mount"></div>`
    near the page root (or in `base.html` if not already present).
  - Modal opens itself on swap via the standard
    `htmx:afterSwap` -> `bootstrap.Modal.getOrCreateInstance(...).show()`
    pattern (search for an existing example before adding new JS).
- Tests in `tests/test_routes/test_transactions.py`:
  - GET preview returns 200 + HTML containing each source row.
  - Preview correctly labels envelope vs discrete actions.
  - Preview disables Confirm button when any envelope target
    canonical is settled.
  - Preview returns 404 for periods the user does not own
    (security -- match existing `_get_accessible_*` patterns).

---

## Critical files (modify)

- `app/models/transaction_template.py` -- column rename only.
- `app/schemas/validation.py` -- field rename + `@validates_schema`.
- `app/services/transaction_service.py` -- **new file**, helper.
- `app/services/carry_forward_service.py` -- envelope branch.
- `app/services/recurrence_engine.py` -- read-only verification of
  skip-on-override.
- `app/routes/transactions.py` -- rename refs, refactor `mark_done`,
  add preview endpoint.
- `app/routes/templates.py` -- rename refs.
- `app/services/credit_workflow.py` / `entry_service.py` /
  `dashboard_service.py` -- rename refs.
- `app/templates/grid/grid.html` -- replace direct-post form with
  modal-mount button.
- `app/templates/grid/_carry_forward_preview_modal.html` -- **new
  file**, the preview modal partial.
- `app/templates/grid/_transaction_full_edit.html`,
  `app/templates/templates/list.html`,
  `app/templates/templates/form.html`,
  `app/templates/companion/_transaction_card.html` -- rename refs.
- `migrations/versions/<new>_rename_track_individual_purchases_to_is_envelope.py`
  -- **new migration**, with downgrade.

## Critical files (read-only reference)

- `app/models/transaction.py:145-169` -- `effective_amount`. Confirms
  settled rows return `Decimal("0")` for balance/subtotal purposes,
  which is exactly the property Option F leans on.
- `app/services/balance_calculator.py:406-419` -- Projected-only
  filter. Same property.
- `app/routes/grid.py:263-282` -- period subtotal. Same filter, so
  cell == subtotal == balance.
- `app/services/entry_service.py:398-416` --
  `compute_actual_from_entries(entries) -> Decimal`. Reuse, do not
  duplicate.
- `app/enums.py` -- `StatusEnum` (`PROJECTED`, `DONE` (a.k.a.
  "Paid"), `RECEIVED`, `SETTLED`, `CREDIT`, `CANCELLED`). Use IDs via
  `ref_cache.status_id(StatusEnum.X)`, never name comparisons.

---

## Verification

End-to-end manual verification (the worked example):

1. Confirm the column rename: open a template form, verify the
   "envelope" / "track individual purchases" checkbox renders, save,
   re-open, persists.
2. Create a tracked-expense template ($100 spending money,
   biweekly). Recurrence generates rows in periods A, B, C.
3. Add $65 of entries against A's row. Period A subtotal includes
   the $100 estimate (effective_amount falls back to estimated for
   Projected with no actual_amount yet).
4. Click `Carry Fwd` on A.
5. Modal opens. Lists "Spending Money: settle at $65, roll $35
   forward to <period B label>." Confirm.
6. Assert in the UI:
   - A's row is Done, displaying "$65" actual.
   - B's row shows `estimated_amount = $135`,
     `is_override = True`, status Projected.
   - No new rows in either period; row count unchanged.
   - Period B subtotal increased by $35 (from the bump).
   - Balance projection: B reduces by $135 instead of $100; A no
     longer contributes (settled, excluded). Net forward cash flow
     unchanged across A+B.
7. Repeat for an untracked template (rent): the modal labels it
   "defer whole." Confirm. Row moved whole into target,
   `is_override=True`, post-33cd21e behavior preserved.
8. Edge case: try `Carry Fwd` from a far-past period whose target
   canonical is already settled. Modal shows BLOCKED row, Confirm
   disabled.
9. Edge case: shadow transfer in source period. Modal labels it
   "transfer move." Confirm. Both shadow legs move whole.
10. Schema rejection: try POSTing a template with income type +
    `is_envelope=True`. Returns 400 with the validation message.
11. Income late-deposit / uncleared-check sanity check (Decision C
    rationale): create an income row with `is_envelope=False`
    (the only legal income case). Carry it forward via the discrete
    path; status flow `Projected -> Received -> Settled` works as
    today. No envelope path involvement.

Targeted test commands per phase (run after each phase, not full
suite):

```bash
pytest tests/test_models/test_transaction_template.py tests/test_routes/test_templates.py -v --tb=short  # Phase 1 + 2
pytest tests/test_services/test_transaction_service.py tests/test_routes/test_transactions.py -v --tb=short  # Phase 3
pytest tests/test_services/test_carry_forward_service.py -v --tb=short  # Phase 4
pytest tests/test_routes/test_transactions.py -k carry_forward -v --tb=short  # Phase 5
```

Final gate (per testing standards): full suite split by directory,
each under the 10-minute pytest hard timeout:

```bash
timeout 720 pytest tests/test_services/ -v --tb=short
timeout 720 pytest tests/test_routes/ -v --tb=short
timeout 720 pytest tests/test_models/ tests/test_integration/ tests/test_adversarial/ tests/test_scripts/ -v --tb=short
pylint app/ --fail-on=E,F
```

Show actual pass/fail counts. Test downgrade direction of the new
migration in a throwaway DB.

---

## What stays from 33cd21e

- The relaxed partial unique index. Required for the discrete
  (untracked) path: rent, utilities, etc., still create
  `is_override=TRUE` siblings when carried forward. The envelope path
  no longer exercises this index, but the discrete path does.
- The `no_autoflush` fix.

33cd21e was an incomplete fix that treated every template the same.
Option F adds the missing branch.

---

## Out of scope (deferred / declined)

- **Production cleanup script (Decision F=2).** Only one sibling pair
  exists; one row already Paid. User cleans up by hand via the UI.
- **DB-level enforcement of the income/envelope rule (Decision C).**
  Marshmallow-only enforcement is the chosen baseline. Denormalized
  `is_income` column or BEFORE-INSERT trigger remain available as
  follow-up if the user wants stricter enforcement later.
- **Per-row "Settle & Roll" button (old Option H from the alternatives
  doc).** The bulk modal covers the use case; per-row is unnecessary
  surface.
- **Bulk fold-up of any other doubled-row situations not produced by
  carry-forward.** Out of scope for this sprint; can be addressed if
  a real case appears.
- **Recurrence-skip behavior change.** No change. Bumped canonicals
  with `is_override=True` continue to block re-generation, which is
  the correct behavior.

---

## Notable risks

1. **Renaming a column touches many files.** Inventory above is
   verified by grep; nothing should be missed, but a
   pre-commit grep for the old name on the working tree is the cheap
   safety check before each commit in Phase 1.
2. **`generate_for_template` returns a list and may be empty** if the
   template's recurrence rule does not apply to the target period
   (e.g., `effective_from` past). The envelope branch must detect the
   empty return and raise the same kind of
   ValidationError as the "settled target" case ("Template not
   active in target period; carry forward not possible. Move
   manually.") -- whole batch refuses, atomic.
3. **Atomicity of the batch.** The existing carry-forward route
   commits once at the end; the envelope branch lives inside that
   same transaction. Any ValidationError inside the loop must trigger
   a rollback (the existing route already returns an error response
   without committing on `NotFoundError`; extend that pattern to
   ValidationError or a new exception type).
4. **Modal -> double-submit.** Existing button uses
   `hx-disabled-elt`; preserve the same pattern on the modal Confirm
   button so a slow click can't run the carry-forward twice.
