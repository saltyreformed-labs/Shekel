> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Implementation Plan: Pay Period CRUD + Continuous Rolling Window

Status: Phases 0, 1, 2, and 3 COMPLETE + GREEN on `dev` (Phase 3 built 2026-06-14; full suite 6225;
not yet PR'd -- the dev -> main PR will cover all of Phase 0-3). The whole feature is now built; the
operation-5 reset design below is realized in `reset_pay_periods`. The Phase 2 build and its
resolved/residual code-review follow-ups are recorded in the "Code review findings" section; the
Phase 3 build is recorded in the "Phase 3 build" section. Design review 2026-06-13 locked four
corrections to the original draft (see Review corrections below).
**Phase 0 COMPLETE on `dev` (2026-06-13):** migrations `d410f6b9caa3` (anchor FK ->
`NO ACTION DEFERRABLE INITIALLY IMMEDIATE`) + `f75485db6757` (`UNIQUE(user_id, period_index)` with a
duplicate pre-flight guard); model edits in `account.py` / `pay_period.py`; new tests
`test_anchor_fk_deferrable.py` + `test_pay_period_index_unique.py` (9 tests); ~20 latent dup-index
bugs in existing test scaffolding fixed at the root. Both migration directions verified,
`flask db check` clean, `pylint app/` 10.00/10, full suite green. **NEXT: Phase 1 (CRUD)** -- and it
must land with Test plan Disciplines 1-4 (invariant checker, balance-after-every-op,
integrity-checker integration, adversarial tests), not just the per-operation bullets.

## Review corrections (2026-06-13, locked via developer Q&A)

These supersede the original draft wherever they conflict; the body below has been updated to match.

1. **Truncate/reset delete via bulk DELETE, not `db.session.delete(p)`.** `PayPeriod.transactions`
   (`pay_period.py:30`) has no `cascade`/`passive_deletes`, so per-object `session.delete()` makes
   SQLAlchemy try to NULL the `NOT NULL` `transactions.pay_period_id` (nullify-on-disassociate) and
   raises `IntegrityError` before the DB `ON DELETE CASCADE` can fire. Use
   `db.session.query(PayPeriod).filter(PayPeriod.id.in_(ids)).delete(synchronize_session=False)` +
   `db.session.expire_all()` so Postgres does the whole cascade in one pass (txns + transfers + both
   shadows + anchor history; SET NULL on recurrence rules) and audit triggers still fire.
2. **Enforce `UNIQUE(user_id, period_index)` at the schema level** (upgrade the non-unique
   `idx_pay_periods_user_index`). This puts the resolver's index-is-unique-and-chronological
   invariant in the schema, defending ALL append paths (extend / regenerate / top-up), not just
   top-up. Every operation keeps indices unique, so it is always satisfiable. The Phase 2 advisory
   lock is then a UX nicety (clean wait-and-noop instead of an `IntegrityError` 500), NOT the sole
   correctness guard. (The original draft's claim that concurrent top-ups dodge
   `uq_pay_periods_user_start` via differing start_dates was wrong: symmetric top-ups compute the
   same `next_start` and DO collide on start_date; the real uncaught window was manual-op-vs-top-up
   with disjoint dates, which the unique index closes.)
3. **Drop `anchor_start_date` from `budget.pay_schedule`.** It equals `min(pay_periods.start_date)`
   and had no consumer; storing derivable data risks drift. The table keeps `cadence_days` + rolling
   config (the genuinely non-derivable data that justifies its existence).
4. **Defer the full reset (operation 5) to its own later phase (Phase 3).** Phase 0's deferrable FK
   still ships now (harmless and future-proof; behaves like immediate `NO ACTION` for every
   non-reset path), but the reset surgery (deferred-FK swap + re-anchor + history rewrite) is built
   only after the CRUD core is proven. Lowest MVP risk.

## Phase 1 build decisions (2026-06-13, locked via developer Q&A)

Refinements decided during the Phase 1 build, after re-validating the plan against the current code.
They supersede the draft where they conflict.

1. **Discard gate broadened to deliberate-status rows.** The block-and-confirm discard gate flags a
   non-deleted to-delete row when `template_id IS NULL` **OR** `is_override=True` **OR**
   `status_id != PROJECTED`. Rationale: `mutations.py:311` sets `is_override` only on an
   amount/period edit, so a future template row a user marked **Credit** or **Cancelled**
   (status-only change) would otherwise be silently discarded and regenerated as Projected. Settled
   rows stay HARD-locked above this gate (overridable confirm never applies to them); the new
   `status_id != PROJECTED` clause adds only Credit/Cancelled, exactly the deliberate-intent rows.
2. **SETTLED_TXN classifier reuses `balance_predicates.settled_status_ids()`** (a cached
   `frozenset{Paid, Received, Settled}`) instead of hand-rolling the `templates.py:696-701`
   subquery. Verified against `ref_seeds.py:99-104` that this set is exactly the `is_settled=True`
   statuses, so it is the canonical, DRY counterpart of the plan's `Status.is_settled.is_(True)`.
3. **`pay_schedule_service.set_rolling` deferred to Phase 2.** The `pay_schedule` table ships with
   the rolling columns now (default off / 52), but the setter that mutates them lands with its
   consumer (the rolling settings UI + `top_up_rolling_window`) in Phase 2. Phase 1 service surface
   is `get_schedule` / `upsert_schedule` / `resolve_cadence`.
4. **`populate_periods_from_active_templates` lives in a new `period_population` module, NOT in
   `recurrence_engine`** as the draft said. The transfer engine already imports the transaction
   engine, so an orchestrator that calls BOTH cannot live inside either without tripping the
   enforced cyclic-import gate (pylint R0401). A neutral module imported by neither keeps the graph
   acyclic; extend / regenerate and the future DRY-follow-up route helpers import it from there.
5. **`scenario_ids` param omitted** from `populate_periods_from_active_templates` and
   `extend_pay_periods` (MVP repopulates the baseline scenario only; the param returns with its
   first real multi-scenario caller -- no gold-plating).

**Phase 1 COMPLETE on `dev` (slices a-f):** (a) migration `af8254074bef` (`budget.pay_schedule` +
audit trigger + cadence backfill, both directions verified), `PaySchedule` model, registry + audit
registration, `pay_schedule_service` (`2087a2b`). (b) `pay_period_admin` lock classifier +
`assert_pay_period_invariants` (`61a589a`). (c) `period_population` + `extend_pay_periods`
(`a903969`). (d) `truncate_pay_periods` (bulk-DELETE + lock + discard gates) + the two new
exceptions (`2e05cd5`). (e) `regenerate_pay_periods` (`0cf78c6`). (f) routes + schemas + manage UI
(`90bd2a3`). `pylint app/` 10.00/10, full suite 6183 passed.

## Code review findings (2026-06-13)

A final high-effort multi-agent review of the Phase 1 diff (`b21aab6..HEAD`). One correctness bug
fixed inline; the rest recorded here to address in a new session.

**FIXED inline:**

- **Regenerate payday off-by-one.** `_regenerate_keep_through_index` used `start_date >= today`, but
  `get_current_period` matches `start_date <= today <= end_date`, so on a payday (start == today)
  the current in-progress period was pulled into the rebuildable tail and wiped. Changed to strict
  `start_date > today`; regression test `test_period_starting_today_is_kept` added.

**RESOLVED in Phase 2 (2026-06-13):**

- **`upsert_schedule` insert race.** FIXED in slice 2a (`335fe21`): `upsert_schedule` now uses
  `pg_insert(...).on_conflict_do_update(constraint="uq_pay_schedule_user", set_={cadence_days})` + a
  `populate_existing` reload, so a concurrent first-generation double-submit cleanly updates instead
  of raising `IntegrityError`. Self-contained (no dependency on the slice-2b lock).
- **DRY: dead single-period classifier.** DONE in slice 0 (`0c7bb2a`): `classify_period_lock`
  delegates to `classify_periods_bulk([period])[period.id]`; the three scalar `EXISTS` helpers were
  deleted. The lock rules now have one encoding.
- **Concurrency TOCTOU (partial).** ADDRESSED in slice 2b (`313d12c`): `truncate` (and `extend` /
  `regenerate` / `top_up`) take the per-user advisory lock `lock_schedule`, serializing the
  classify-then-DELETE window against all other STRUCTURAL pay-period ops. RESIDUAL (deliberately
  NOT closed): the lock does NOT serialize truncate vs a concurrent `mark-done` (settle) or anchor
  true-up, since those hot paths do not take it -- so the settled-row classify-then-DELETE window
  stays theoretically open. Negligible for the solo operator (Phase 1 assessment) and closing it
  would mean instrumenting the settlement path (rule-13 gold-plating). The anchor variant fails
  SAFE: the NO ACTION anchor FK turns it into a loud `IntegrityError`, never silent corruption.

**OPEN follow-ups (later):**

- **Truncate can delete the current in-progress period.** Only historical/settled/anchor are
  hard-locked, so a `keep_through_index` below the current period deletes it (the user picks the
  boundary, so this is deliberate today, but `get_current_period` then returns `None` until the next
  generate). Consider a soft confirm if undesired. (Not concurrency; out of Phase 2 scope.)

**VERIFIED NOT BUGS (no action):** the lock/discard scan is intentionally scenario-unfiltered (the
cascade deletes all scenarios' rows, so gating on any is correct); credit-payback orphan is gated
(paybacks are `template_id IS NULL`, and a payback is always `source_period + 1` so a tail cut keeps
both on the same side); anchor-history audit rows live at past/current positions, never the deleted
future tail; `resolve_cadence` cannot mis-infer because periods are only ever created by generation
(consistent cadence); `extend` deliberately does not persist cadence (it is a one-off append, not a
cadence-defining op).

## Phase 3 build (2026-06-14)

The bounded full reset (operation 5). Validated the operation-5 spec against the current code first
(its cited signatures had drifted past Phases 1-2) and resolved two design forks with the developer
before building. Built in two committed slices; `pylint app/` 10.00/10 and the full suite green at
each. **No migration:** Phase 0's `NO ACTION DEFERRABLE INITIALLY IMMEDIATE` anchor FK was confirmed
live in dev (`confdeltype='a'`, `condeferrable=true`, `condeferred=false`) and is the ONLY
deferrable constraint in the database, so reset reuses it as designed.

**Design forks (developer-decided):**

1. **Re-anchor through `anchor_service` without its internal commit (Fork A -> A1).**
   `apply_anchor_true_up` commits per call, which would fire the deferred FK mid-reset while other
   accounts still dangle. Extracted a flush-only kernel `anchor_service.stage_anchor_true_up` (set
   the two anchor columns + append the history row; no entry-clear, no commit, no outcome
   translation). `apply_anchor_true_up` now wraps it -- route behavior byte-identical (verified by
   the 282-test anchor/account/grid/savings run). Reset calls the kernel per account and its route
   commits once. `account_service._resolve_anchor_period_id` promoted to public
   `resolve_anchor_period_id` (reset is a second cross-module caller).

2. **`SET CONSTRAINTS` under the ORM-only rule (Fork B -> B1).** There is no SQLAlchemy construct
   for `SET CONSTRAINTS` (transaction-control, not a query), so it is issued via `sa.text` with a
   module-level constant -- the same way `lock_schedule` reaches for `pg_advisory_xact_lock`.
   Empirically verified: the **schema-qualified** name
   `SET CONSTRAINTS budget.accounts_current_anchor_period_id_fkey DEFERRED` works; the unqualified
   name fails (the connection search_path is `"$user", public`, no `budget`);
   `SET CONSTRAINTS ALL DEFERRED` works but was rejected in favor of naming the one FK (future-proof
   against another deferrable constraint being added).

**`reset_pay_periods(user_id, new_start_date, num_periods, cadence_days)`** (one transaction the
route commits): (1) zero-settled gate -- refuse with `PayPeriodResetBlocked` if any settled,
non-deleted txn exists (scoped via `PayPeriod` since `Transaction` has no `user_id`; reuses
`balance_predicates.settled_status_ids`, matching the lock classifier's notion of "settled"; settled
transfers are caught via their settled shadows); (2) per-user advisory `lock_schedule`; (3) defer
the anchor FK; (4) capture each account's anchor balance + the rule ids carrying an explicit
`start_period_id` (the cascade NULLs them, and a legitimately-NULL rule is indistinguishable after
the wipe); (5) bulk-DELETE all the user's periods (same pattern as truncate: cascade clears
txns/transfers/shadows/anchor history, SET NULL on rule start periods) + `expire_all`; (6) generate
from `new_start_date` (index 0); (7) re-anchor each account via `stage_anchor_true_up` (balance
preserved, fresh `origination (pay-period reset)` history row) + re-point the captured rules to the
new first period + repopulate templates; (8) upsert the new cadence. The route's commit validates
the deferred FK. A user with no accounts (a brand-new not-yet-anchored user) resets cleanly
(re-anchor is a no-op). `recurrence_rules.start_period_id` re-pointing is NOT needed for correct
repopulation (`populate_periods_from_active_templates` always passes `effective_from`, so generation
ignores `start_period_id`); it is done for semantic fidelity and so the new first period classifies
as a RECURRENCE_ANCHOR.

**Slice a (`6866e78`):** `stage_anchor_true_up` extraction + `resolve_anchor_period_id` promotion +
`PayPeriodResetBlocked` exception + `reset_pay_periods` + the four private helpers
(`_settled_transaction_count`, `_rule_ids_with_start_period`, `_reanchor_accounts`,
`_repoint_recurrence_rules`) + the `_DEFER_ANCHOR_FK_SQL` constant. Tests `test_pay_period_reset.py`
(12, all four disciplines): wipe-incl-anchor + deferred-FK commit, balance preserved + recomputed
(`balance_as_of_date`), txn + transfer repopulation (2-shadow invariant), recurrence re-point,
multi-account re-anchor, not-yet-anchored user, persists-cadence, settled-blocks-unchanged,
soft-deleted-settled-allowed, invalid-cadence-rolls-back. Full suite 6218.

**Slice b (`37d9431`):** `POST /pay-periods/reset` route + `PayPeriodResetSchema` (new_start_date /
num_periods / cadence_days + required `confirm`) + `can_reset_pay_periods` public predicate +
`pp_can_reset` settings context + the danger-zone reset card in `_pay_periods_manage.html` (shown
only to zero-settled users, else a Regenerate-instead note). Tests (+7): rebuild + re-anchor via
HTTP, unconfirmed-refused, settled-blocks, schema-rejection, companion-404 (IDOR), reset-form
shown/hidden gating. Full suite 6225.

## Context

Shekel can **generate** pay periods but cannot edit or delete them (`app/routes/pay_periods.py`
exposes only `GET/POST /pay-periods/generate`). If a user mis-generates, their payday shifts, or
they run out of forward periods, there is no recovery path. This adds the missing lifecycle:
**extend** the schedule forward, **truncate** (delete from the end), **regenerate** a wrong tail,
and an opt-in **continuous rolling window** that keeps N periods always generated ahead of today.

Three facts from the codebase shape the whole design and are non-negotiable:

1. **A pay period is only `(start_date, end_date, period_index)` -- cadence is never stored.**
   `cadence_days` is a generation-time argument (`pay_period_service.py:70`). Extend and continuous
   mode have nothing to continue from unless cadence is persisted. We persist it (new table).
2. **`period_index` must equal calendar order.** The balance engine walks periods by `period_index`
   ascending and assumes that is chronological (`balance_resolver.py:879`). Only **tail-append** and
   **tail-truncate** preserve this. Mid-series date-edit/delete silently corrupts as-of balances.
   This is why we do NOT build per-period date editing.
3. **Recurring transactions are eager real rows; new periods are born empty.**
   `generate_pay_periods` creates blank periods and does NOT call the recurrence engine
   (`recurrence_engine.generate_for_template`, `recurrence_engine.py:131`). Extend/regenerate must
   re-run the engine so new periods get their rent/paychecks. Templates survive deletion, so
   template rows are regenerable; hand-entered ad-hoc rows and overrides are NOT.

## Decisions (locked)

- **Edit model:** Regenerate, **no per-period date editing**. "Fix a mistake" = rebuild the unlocked
  future tail from a new start date + cadence; locked/historical periods stay frozen.
- **Continuous trigger:** **On-request top-up** at grid + dashboard entry (no scheduler exists --
  Gunicorn only). Cheap (one count query), idempotent. Correctness under concurrency comes from the
  `UNIQUE(user_id, period_index)` constraint (Review correction 2); the Postgres advisory lock is a
  UX nicety layered on top (clean wait-and-noop instead of an `IntegrityError` 500).
- **Rolling window framing:** **Count-based** -- "always keep N periods ahead of the current one."
- **Config storage:** **New normalized `budget.pay_schedule` table** (one row per user), holding
  cadence + rolling on/off + target count (NO `anchor_start_date` -- dropped per Review correction 3
  as derivable from `min(pay_periods.start_date)`).
- **Destructive-op data safety (recommended default, not separately asked):** **block-and-confirm.**
  Truncate/regenerate refuse if the wiped window holds hand-entered (`template_id IS NULL`) or
  override (`is_override=True`) transactions, until the user passes an explicit
  `confirm_discard=true`. Never silent. The settled/historical/anchor lock (below) is hard-blocking
  and cannot be overridden at all.
- **Repopulation scope (recommended default):** baseline scenario only for MVP; include both
  transaction templates AND transfer templates (so new periods do not silently miss recurring
  transfers). Multi-scenario reserved for later.
- **Full reset (first-time setup correction; built in Phase 3 per Review correction 4):** a bounded
  `reset_pay_periods` rebuilds the WHOLE schedule including the anchor period (which Regenerate
  cannot touch), re-anchoring through the existing `anchor_service` and preserving the account + its
  balance. Offered ONLY when the user has no settled transactions. This closes the deferred
  pay-period reset/realign feature (see `docs/audits/pylint-cleanup/deep-quality-hunt.md`, DH-#39 /
  Batch Y, and the 2026-06-10 design feedback note in that register).

## New model: `budget.pay_schedule`

New file `app/models/pay_schedule.py`, class `PaySchedule`, `__table_args__ schema="budget"`. Mirror
the constraint/naming style of `app/models/pay_period.py:12-22`.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | Integer PK | |
| `user_id` | Integer FK `auth.users.id` CASCADE | `UserScopedMixin`; `uq_pay_schedule_user` UNIQUE -- one schedule per user |
| `cadence_days` | Integer NOT NULL | `ck_pay_schedule_cadence_range`: BETWEEN 1 AND 365 |
| `rolling_enabled` | Boolean NOT NULL, server_default false | continuous mode on/off |
| `rolling_target_periods` | Integer NOT NULL, server_default `DEFAULT_PAY_PERIOD_HORIZON` (52) | `ck_pay_schedule_positive_target`: > 0 |
| `created_at` | TIMESTAMPTZ | `CreatedAtMixin` |

- Register `("budget", "pay_schedule")` in `app/audit_infrastructure.py` AUDITED_TABLES (database
  rule: every new `budget` table is audited).
- New service `app/services/pay_schedule_service.py` (Flask-isolated): `get_schedule(user_id)`,
  `upsert_schedule(user_id, cadence_days)` (called on generate/regenerate),
  `set_rolling(user_id, enabled, target_periods)` (called from settings), `resolve_cadence(user_id)`
  (schedule cadence, else fall back to inferring from the last period's length for legacy users).

## The operation set

All new mutation logic in new module `app/services/pay_period_admin.py` (keeps destructive paths out
of the heavily-imported read/generate `pay_period_service.py`). All new routes extend
`pay_periods_bp` (`app/routes/pay_periods.py`), `@login_required @require_owner`,
**full-POST + redirect** (matches the existing `generate()` at `pay_periods.py:61-64`; mutations are
infrequent settings-page actions, not HTMX-worthy). The route commits; services flush only.

### 1. Extend (append) -- `POST /pay-periods/extend`

`extend_pay_periods(user_id, num_periods, cadence_days=None, scenario_ids=None)`:

1. `last = get_all_periods(user_id)[-1]` (`pay_period_service.py:197`). Empty -> `ValidationError`
   ("generate your first schedule before extending").
2. cadence = explicit arg, else `pay_schedule_service.resolve_cadence(user_id)`.
3. `next_start = last.end_date + 1 day`; call
   `generate_pay_periods(user_id, next_start, num_periods, cadence_days)` -- reuses index
   continuation + `_reject_overlapping_batch` (`pay_period_service.py:26-67`) wholesale.
4. **Repopulate the new periods only** (see Template repopulation).

### 2. Truncate (delete tail) -- `POST /pay-periods/truncate`

`truncate_pay_periods(user_id, keep_through_index, confirm_discard=False)`:

1. `to_delete = [p for p in get_all_periods(user_id) if p.period_index > keep_through_index]`. Empty
   -> idempotent no-op.
2. Run the **bulk lock classifier** over `to_delete`. Any hard-lock (historical / settled / account
   anchor / rule anchor) -> raise `PayPeriodLocked(blocking_periods)`; delete nothing.
3. If any to-delete period has hand-entered/override rows and `confirm_discard` is False -> raise
   `PayPeriodDiscardRequired(count)`; delete nothing.
4. **Bulk DELETE, not per-object `session.delete()`** (see Review correction 1):
   `db.session.query(PayPeriod).filter(PayPeriod.id.in_([p.id for p in to_delete])) .delete(synchronize_session=False)`
   then `db.session.expire_all()`. Postgres does the whole cascade in one pass: transactions +
   transfers (+ both shadows, preserving transfer invariants, `transfer.py:123-131`) + anchor
   history, SET NULL on `recurrence_rules.start_period_id`; audit triggers (DB-level) still fire.
   Per-object `session.delete()` would instead trip SQLAlchemy's nullify-on-disassociate against the
   `NOT NULL` `transactions.pay_period_id` and raise `IntegrityError`. The anchor/rule guards
   guarantee the `accounts.current_anchor_period_id` FK is never hit via this path.

### 3. Regenerate -- `POST /pay-periods/regenerate`

`regenerate_pay_periods(user_id, new_start_date, num_periods, cadence_days, confirm_discard=False)`:
= compute the **mutable boundary** (lowest `period_index` that is both unlocked and
`start_date >= today`) -> `truncate_pay_periods(keep_through_index = boundary - 1, confirm_discard)`
-> `generate_pay_periods(new_start_date, num_periods, cadence_days)` -> repopulate ->
`pay_schedule_service.upsert_schedule(user_id, cadence_days)` with the new cadence. `new_start_date`
must be > the last RETAINED period's `end_date` (re-checked by `_reject_overlapping_batch`). If a
settled/ historical/anchor period sits inside the requested window, the truncate guard refuses (you
cannot rewrite history under a settled paycheck).

### 4. Continuous rolling top-up -- `top_up_rolling_window(user_id, now=None)`

1. Read `pay_schedule_service.get_schedule(user_id)`. `rolling_enabled` false -> return 0 (zero
   extra queries in the common disabled path; no lock taken).
2. Cheap pre-check (no lock): `future = COUNT(pay_periods WHERE user_id=? AND end_date >= today)`.
   If `future >= rolling_target_periods` -> return 0. This `end_date >= today` count INCLUDES the
   current period, so "keep N ahead" means N counting the current one -- state that plainly in the
   settings help text.
3. Deficit exists -> `SELECT pg_advisory_xact_lock(<ROLLING_LOCK_NAMESPACE>, user_id)` (the
   two-argument `(int4, int4)` form with a constant namespace key, NOT a hashed single key, so this
   feature cannot collide with any other advisory-lock user keyed on the same id;
   transaction-scoped, auto-released on commit). Then **re-count under the lock** (double-checked:
   another request may have just filled the window) and recompute the deficit.
4. `extend_pay_periods(user_id, deficit = target - future, cadence)`.

- **Correctness vs. UX:** the `UNIQUE(user_id, period_index)` constraint (Review correction 2) is
  the actual guard against duplicate indices, and it covers ALL append paths (manual extend /
  regenerate / top-up), not just this one. The advisory lock here is a UX nicety: it serializes
  concurrent top-ups so the loser cleanly re-reads a full window and does nothing, instead of racing
  to an `IntegrityError` 500. Do not document the lock as the sole correctness guard.
- **Trigger:** call from the top of `grid.index` (`app/routes/grid.py`) and the dashboard route.
  Owner-only routes already (`@require_owner` 404s companions, `grid.py`/`auth_helpers.py:77`), so
  no extra companion guard is needed; just `if current_user.is_authenticated`. These are the only
  routes that consume future periods.

### 5. Full reset (first-time setup correction) -- `POST /pay-periods/reset` -- DEFERRED TO PHASE 3

Per Review correction 4, this operation is built in its own later phase, AFTER extend / truncate /
regenerate / continuous are shipped and proven. Phase 0's deferrable FK still ships up front (it is
inert for every non-reset path), so no second FK migration is needed when reset lands. Spec retained
here so the design is not lost.

`reset_pay_periods(user_id, new_start_date, num_periods, cadence_days)`. Closes the deferred reset/
realign feature: it rebuilds the WHOLE schedule, INCLUDING the anchor period, which Regenerate
cannot (the `ACCOUNT_ANCHOR` lock). The obstacle is structural, not the lock: the account FK is NOT
NULL and the app is forward-only, so a corrected schedule cannot coexist with the wrong one -- the
reset must delete then recreate, leaving the anchor briefly pointing at nothing.
**Bounded for safety:** refuse unless the user has ZERO settled transactions anywhere (reuse the
classifier's `is_settled` subquery across all the user's transactions). This is a first-time-setup
correction, NOT a tool for users with real history (they use Regenerate). One transaction:

1. `SET CONSTRAINTS <anchor fk> DEFERRED` (defer the anchor FK to commit -- see Migration 1).
2. Capture each account's `current_anchor_balance` (a real dollar value, preserved across the
   reset).
3. Delete all the user's pay periods via the SAME bulk DELETE the truncate path uses (Review
   correction 1): `query(PayPeriod).filter_by(user_id=...).delete(synchronize_session=False)` +
   `expire_all()`. CASCADE clears transactions/transfers/anchor-history; the
   `recurrence_rules.start_period_id` SET NULL nulls any rule anchors.
4. `generate_pay_periods(user_id, new_start_date, num_periods, cadence_days)`.
5. Re-establish each account's anchor on the new schedule: re-point `current_anchor_period_id` using
   the SAME resolution as account creation (`account_service._resolve_anchor_period_id:48-87` -- the
   new period containing today, else the earliest), restore the preserved `current_anchor_balance`,
   and write a fresh origination `AccountAnchorHistory` row. Reuse
   `anchor_service.apply_anchor_true_up` (`anchor_service.py:166-258`) rather than hand-mutating the
   columns.
6. Re-point any `recurrence_rules.start_period_id` nulled in step 3 to the new first period, then
   repopulate templates via the same `populate_periods_from_active_templates` extend/regenerate use.
7. Commit -> the deferred anchor FK validates (every account now points at a live new period).
The anchor lock stays a HARD block for Truncate/Regenerate; reset is the ONLY path that deliberately
re-anchors, and it does so THROUGH `anchor_service`, never around the lock.

### Per-period date editing: deliberately NOT built

Editing any non-tail period's dates reorders calendar position without reordering `period_index`,
making `balance_resolver` pick the wrong period and silently drop transactions from as-of balances,
and can create overlapping spans (nondeterministic `get_current_period`,
`pay_period_service.py:150`). Regenerate serves the real need (a drifted schedule) coherently.
Documented non-feature.

## The lock predicate (single reusable classifier)

In `pay_period_admin.py`. `PeriodLockReason` enum: `HISTORICAL`, `SETTLED_TXN`, `ACCOUNT_ANCHOR`,
`RECURRENCE_ANCHOR`. `classify_period_lock(period, as_of=None) -> PeriodLockReason | None` returns
the first reason or None (mutable). Checks, in order:

- `end_date < today` -> HISTORICAL.
- Holds a settled txn: **adapt** (do NOT copy verbatim) the `Status.id WHERE is_settled.is_(True)`
  scalar-subquery from `templates.py:696-701`. That site uses `.notin_(...)` to delete non-settled
  rows and does not filter `is_deleted`; the classifier needs the inverse: an `EXISTS` over
  `Transaction.pay_period_id == period.id`, `status_id.in_(settled_status_ids)`,
  `is_deleted.is_(False)` (a soft-deleted settled row must NOT lock) -> SETTLED_TXN.
- `EXISTS(Account WHERE current_anchor_period_id == period.id)` -> ACCOUNT_ANCHOR (the NOT-NULL FK
  landmine, `account.py:73-86`).
- `EXISTS(RecurrenceRule WHERE start_period_id == period.id)` -> RECURRENCE_ANCHOR (SET NULL would
  silently break the rule's offset math, `recurrence_rule.py:63-67`).

Add `classify_periods_bulk(periods)` for the truncate path (4 set-queries + in-Python date check) to
avoid N+1; keep `classify_period_lock` as the single-period source of truth. PROJECTED and future
ad-hoc rows are intentionally NOT lock reasons -- they are the mutable payload (handled by the
separate block-and-confirm `confirm_discard` gate, which is overridable; locks are not).

## Migrations

**Migration 1 (Phase 0, ships alone, prerequisite):** retarget the
`budget.accounts.current_anchor_period_id` FK from `SET NULL` to
**`NO ACTION DEFERRABLE INITIALLY IMMEDIATE`** (NOT plain `RESTRICT`). **Reuse the C-43 helpers**
from `migrations/versions/b4b588a49a0c_*` (`_drop_and_recreate_fk`, `_assert_fk_ondelete`,
`_constraint_exists`), extended to emit the `DEFERRABLE INITIALLY IMMEDIATE` clause. Verify the live
constraint name with `\d+ budget.accounts` first (likely Alembic-default
`accounts_current_anchor_period_id_fkey`). Working `downgrade()` restores `SET NULL` (pure DDL).
`Review: <name>, <date>` docstring (constraint recreate). Update the now-stale comment at
`account.py:73-86`. Why `NO ACTION DEFERRABLE`, not `RESTRICT`: both defuse the latent
NOT-NULL/SET-NULL landmine and (with the app-level anchor lock) block any accidental delete of an
anchor period at statement end. But `RESTRICT` is checked immediately and cannot be deferred --
which would make the atomic full reset (operation 5) impossible, since it must delete the old anchor
period and re-point the anchor to a new one inside one transaction. `NO ACTION DEFERRABLE` lets ONLY
the reset transaction `SET CONSTRAINTS ... DEFERRED` so the FK is validated at commit (by which
point the anchor points at a fresh period); every other path keeps fail-fast immediate checking.
Equally safe, strictly more flexible.

**Migration 1b (Phase 0, ships with Phase 0):** upgrade the non-unique `idx_pay_periods_user_index`
to a UNIQUE constraint/index on `(user_id, period_index)` -- name `uq_pay_periods_user_index`
(Review correction 2). FIRST verify no existing user has duplicate `(user_id, period_index)` pairs
(every shipped path assigns `max+1`, so production should be clean): run a
`SELECT user_id, period_index, COUNT(*) ... GROUP BY ... HAVING COUNT(*) > 1` guard and
`raise RuntimeError` with the offending rows if any survive (database rule: constraint additions on
a populated table verify first). `upgrade()` drops the plain index then creates the unique one;
`downgrade()` restores the plain index. Pure DDL both directions; no `Review:` line needed
(additive, non-destructive of data).

**Migration 2 (Phase 1):** create `budget.pay_schedule` (named constraints above; no
`anchor_start_date` column -- Review correction 3) +
**backfill one row per user who already has pay periods** (backfills belong in the migration):
`cadence_days` = last period length `(end_date - start_date) + 1`, `rolling_enabled` = false,
`rolling_target_periods` = 52. Users with no periods get no row. `downgrade()` drops the table.

## Routes, schemas, settings

- Routes (all in `pay_periods_bp`): `POST /pay-periods/extend`, `/truncate`, `/regenerate`, plus
  `POST /pay-periods/schedule` (rolling config). New Marshmallow schemas in
  `app/schemas/validation/pay_periods.py`: `PayPeriodExtendSchema`, `PayPeriodTruncateSchema`,
  `PayPeriodRegenerateSchema`, `PayScheduleSchema` (rolling_enabled bool, rolling_target_periods
  1-260, cadence_days 1-365). Follow `PayPeriodGenerateSchema` (`pay_periods.py:12`) style and the
  422-on-error route pattern.
- `/pay-periods/schedule` writes via `pay_schedule_service.set_rolling(...)` (NOT the
  `_SIMPLE_SETTINGS_FIELDS` allowlist in `settings.py`, since config now lives in `pay_schedule`,
  not `user_settings`).
- On the existing `generate()` route, also `upsert_schedule(user_id, cadence_days)` so cadence is
  captured authoritatively at first generation.

## Templates / UI

Extend the existing pay-periods settings section. `app/templates/settings/_pay_periods.html` keeps
the generate form and gains a new manage partial `settings/_pay_periods_manage.html`:

- A period list rendering each period's `label` (`pay_period.py` property) with a lock badge
  (mutable / historical / settled / anchor) from the classifier.
- Extend form (count). Truncate form (pick "keep through" period) with a confirm step if discard
  needed. Regenerate form (new start + cadence + count) with the same confirm gate.
- Rolling-window controls: enable toggle + target-count input -> `POST /pay-periods/schedule`.
The settings route `show()` (`settings.py:69`) passes the period list + schedule for the
`pay-periods` section (currently passes only `errors={}`).

## Template repopulation (extend & regenerate)

New helper:

```python
recurrence_engine.populate_periods_from_active_templates(
    user_id, periods, scenario_ids=None, effective_from=None
) -> int
```

1. Baseline scenario via `scenario_resolver.get_baseline_scenario(user_id)` (matches how
   `templates.py:324-331` populates).
2. Active `TransactionTemplate`s (filter `is_active` -- `IsActiveMixin`; archived =
   `is_active=False`) ->
   `recurrence_engine.generate_for_template(template, periods, scenario.id, effective_from=periods[0].start_date)`
   for each (`recurrence_engine.py:131`).
3. Active `TransferTemplate`s -> `transfer_recurrence.generate_for_template(...)` (same signature,
   in `app/services/transfer_recurrence.py:46`; delegates to `transfer_service.create_transfer`,
   keeping the transfer + exactly-2-shadows invariant, CLAUDE.md transfer invariants).
Cannot violate the unique partial index `idx_transactions_template_period_scenario`
(`migrations/versions/c79bfaef598e_*`, scoped
`template_id IS NOT NULL AND is_deleted=FALSE AND is_override=FALSE`) because new/just-truncated
periods are empty, and the shared `should_skip_period` (`app/services/_recurrence_common.py:181`)
skips any already-populated period -- so retried top-ups create nothing.

**DRY opportunity (out of scope -- do NOT build now, but recorded so it is not forgotten):** the
"resolve baseline scenario -> `get_all_periods` -> `generate_for_template`" idiom already exists,
duplicated across the route layer -- `app/routes/_transfer_creation_helpers.py:217`
(`generate_transfers_for_all_periods`), the inline block at `templates.py:324-331`,
`app/routes/investment.py`, and `app/routes/loan/payment_transfer.py`. The new service-layer
`populate_periods_from_active_templates` is better placed than all of them (Flask-isolated; handles
BOTH transaction and transfer templates in one pass over a SPECIFIC period subset). Once it exists,
those route helpers could delegate to it to kill the duplication. Tracked as a follow-up; not part
of this work's scope.

## Phased rollout (tight scope, no gold-plating)

- **Phase 0:** Migration 1 (anchor FK -> `NO ACTION DEFERRABLE INITIALLY IMMEDIATE`) + Migration 1b
  (`UNIQUE(user_id, period_index)`) + model comment fix + FK/unique-index tests. Ships alone.
- **Phase 1 (CRUD):** Migration 2 (`pay_schedule` + backfill, no `anchor_start_date`); `PaySchedule`
  model + audit registration; `pay_schedule_service`; `pay_period_admin` (classifier + extend +
  truncate + regenerate with block-and-confirm, all destructive deletes via bulk DELETE);
  `populate_periods_from_active_templates`; routes + schemas; manage UI; tests.
  **Full reset is NOT in Phase 1** (moved to Phase 3).
- **Phase 2 (continuous):** `top_up_rolling_window` (advisory lock as a UX nicety -- the unique
  index from Phase 0 is the correctness guard); rolling settings UI; grid + dashboard trigger hooks;
  concurrency/idempotency tests.
- **Phase 3 (full reset, deferred):** `reset_pay_periods` bounded to no-settled, deferred-FK atomic
  swap + re-anchor via `anchor_service` + recurrence re-pointing; `POST /pay-periods/reset` + schema
  - UI gated to zero-settled users; tests. The Phase 0 deferrable FK is already in place, so no new
  migration is needed here. Closes DH-#39's first-time-setup half.
- **Out of scope:** per-period date editing; multi-scenario repopulation; carry-forward of ad-hoc
  rows on regenerate; any scheduler/Celery infra; the route-helper DRY consolidation noted under
  Template repopulation. Each is a documented later-if-needed.

## Test plan

**Why testing is the load-bearing safeguard for this feature (read first).** A pay period is the
spine of every financial number in Shekel: the balance resolver walks periods by `period_index` and
trusts that order to be chronological, and every transaction, paycheck, transfer, and as-of balance
hangs off a period. Corrupt the period structure -- a duplicate or non-monotonic index, a deleted
anchor, an orphaned shadow, a wiped settled row -- and the corruption does not announce itself: it
silently produces a WRONG balance that looks plausible. This is the one feature whose bugs are both
the most likely to be invisible and the most expensive (real money mismanaged). There is no QA team
and no reviewer; the tests are the only thing standing between a defect and production. So the bar
here is higher than "each function has a test": the tests must make period corruption IMPOSSIBLE to
ship undetected. Three non-negotiable disciplines below (invariant checker, balance-correctness
after every mutation, corruption-attempt tests) exist specifically to meet that bar; the
per-operation bullets are necessary but not sufficient on their own.

Evidence this is not hypothetical: adding the Phase 0 `UNIQUE(user_id, period_index)` constraint
immediately surfaced ~20 pre-existing test setups across 5 files that had been silently creating
duplicate-index periods (the invariant was violated and nothing caught it). Each was a latent
landmine the schema now forbids. If test SCAFFOLDING drifted that far unnoticed, app code can too --
which is exactly why the invariant must be asserted continuously, not spot-checked.

New: `tests/test_services/test_pay_period_admin.py`,
`tests/test_services/test_pay_schedule_service.py`, `tests/test_routes/test_pay_period_admin.py`,
`tests/test_models/test_anchor_fk_deferrable.py` (NOT `_restrict` -- the FK is
`NO ACTION DEFERRABLE`, see below), `tests/test_models/test_pay_period_index_unique.py`. Reuse
`bare_user`, `bare_periods`, `bare_auth_client` (`conftest.py:831-901`); Decimal-from-string money;
exact assertions; docstrings.

### Discipline 1: the reusable invariant checker (run after EVERY mutation)

New helper
`tests/helpers/pay_period_invariants.py::assert_pay_period_invariants(db_session, user_id)` -- the
single source of truth for "the user's period structure is not corrupt." Called at the END of every
extend / truncate / regenerate / top-up / reset test (and after the route-level POST in the route
tests). Asserts, for the user:

1. **Index uniqueness:** `COUNT(*) == COUNT(DISTINCT period_index)` (the schema enforces it now;
   this catches any path that bypasses the ORM).
2. **Index == calendar order:** sorting periods by `period_index` yields strictly ascending
   `start_date` AND strictly ascending `end_date` -- the exact property `balance_resolver` depends
   on. This is the assertion that would have caught a silent as-of-balance corruption.
3. **No date overlaps and no unintended gaps** in the retained window (reuse the BA-03/BA-04 logic;
   see Discipline 3).
4. **Anchor integrity:** every account's `current_anchor_period_id` points at a live period owned by
   the same user.
5. **Transfer invariant intact:** every transfer still has exactly two shadow transactions, both in
   the same (still-existing) period as their parent; no orphaned shadow, no shadow without sibling
   (CLAUDE.md transfer invariants).
6. **No orphaned template rows** referencing a deleted period.

A test that mutates periods and does NOT call this helper is incomplete. The helper is DRY (one
definition, asserted everywhere) and is the mechanism that makes "corruption cannot ship" structural
rather than aspirational.

### Discipline 2: assert the MONEY, not just the structure

Because the whole risk is "wrong periods -> wrong balances," every CRUD operation test must also
assert that **as-of balances are still correct after the mutation**, computed by hand from the
anchor (testing-standards: service tests assert exact computed values with the arithmetic shown,
never `> 0`). Concretely: seed a known anchor + known recurring rows, run the operation, then assert
`balance_resolver.balance_as_of_date(...)` returns the hand-computed Decimal for at least one date
in the affected window AND one date in the retained (locked) window. Structure passing while
balances drift is the precise failure mode this catches.

### Discipline 3: wire the operations into the existing integrity checker

`scripts/integrity_check.py` already detects period-structure anomalies (BA-02 anchor-beyond-last,
BA-03 index gap, BA-04 date overlap, plus the FK/orphan checks). After each CRUD operation in the
service tests, run `check_balance_anomalies(db_session)` (and the FK/orphan checks) and assert ALL
pass -- the operations must never produce a state the production integrity checker would flag. This
connects the new feature to the safety net that already guards the database and means a regression
trips two independent tripwires (the invariant helper and the shipped checker).

### Per-operation assertions (necessary, not sufficient -- pair each with Disciplines 1-3)

- **Classifier:** future-empty -> None; historical -> HISTORICAL; Paid txn -> SETTLED_TXN
  (PROJECTED-only NOT locked; soft-deleted settled NOT locked); account anchor -> ACCOUNT_ANCHOR;
  rule anchor -> RECURRENCE_ANCHOR; `classify_periods_bulk` == N single calls.
- **Extend:** indices continue contiguously; `next_start == last.end + 1`; cadence arg > schedule >
  inferred; new periods get one PROJECTED row per active `every_period` template; archived template
  -> none; empty schedule -> ValidationError.
- **Truncate:** deletes only `index > K`; CASCADE removes txns + transfers (+ shadows); each hard-
  lock raises `PayPeriodLocked` and deletes nothing; ad-hoc/override without confirm raises
  `PayPeriodDiscardRequired`, with confirm proceeds; idempotent no-op past max index.
- **Regenerate:** locked prefix retained, mutable tail rebuilt + repopulated; refuses when a settled
  period is inside the window; resolver as-of balances still correct after rebuild.
- **Full reset (PHASE 3 -- tests land with that phase, not Phase 1):** refuses when ANY settled
  transaction exists; on a no-settled schedule, wipes (bulk DELETE) + rebuilds all periods INCLUDING
  the old anchor period; re-points each account's `current_anchor_period_id` to the new current
  period with the balance preserved; writes a fresh origination history row; the deferred anchor FK
  validates at commit (no orphan, no NOT-NULL violation) -- assert the
  `SET CONSTRAINTS ... DEFERRED` path inside one transaction; recurrence-rule anchors re-pointed +
  templates repopulated; resolver as-of balances correct against the new schedule. Assert a
  brand-new (not-yet-anchored) user can full-reset too.
- **Top-up:** disabled -> 0, no write query (and no advisory lock taken); full window -> 0; deficit
  D -> exactly D, second call -> 0; two sequential top-ups against the same short state never
  produce a duplicate `period_index` (`COUNT(*) == COUNT(DISTINCT period_index)`); hitting `/grid`
  with rolling enabled + deficit creates periods.
- **Unique index (`UNIQUE(user_id, period_index)`):** inserting a second period with an existing
  `(user_id, period_index)` raises `IntegrityError`; the same `period_index` for a DIFFERENT user is
  allowed; mirror the constraint-introspection style of
  `tests/test_models/test_c43_ondelete_and_naming_convention.py`.
- **FK migration:** the anchor FK is `NO ACTION DEFERRABLE INITIALLY IMMEDIATE`, so assert
  `pg_constraint.confdeltype == 'a'` (NO ACTION) AND `condeferrable IS TRUE` AND
  `condeferred IS FALSE` -- NOT `'r'` (mirror
  `tests/test_models/test_c43_ondelete_and_naming_convention.py`); deleting an anchor period
  (immediate check) raises IntegrityError, while a transaction that `SET CONSTRAINTS ... DEFERRED`
  can delete-then-re-point within one commit (this last assertion is Phase 3).
- **Migration pre-flight guards (prove the safety nets fire):** the unique-index migration's
  `upgrade()` must RAISE `RuntimeError` naming the offending rows when the data already holds a
  duplicate `(user_id, period_index)` (drop the constraint, insert a dup via the test session, run
  `upgrade()`, assert the raise + that the constraint was not created; restore in `finally`). Mirror
  the established pattern in `test_c19_credit_payback_unique.py::TestMigrationPreFlightCheck`. Same
  spirit for the `pay_schedule` backfill: assert the backfilled `cadence_days` equals the real last
  period length for a user with periods, and that a no-periods user gets no row.

### Discipline 4: adversarial / corruption-attempt tests (assert the bad state is REFUSED)

For a feature whose failure mode is silent corruption, the negative tests carry as much weight as
the happy path. Each must assert the corrupting action is BLOCKED (raises / 404 / 422 / no-op), and
then call the invariant checker to confirm nothing changed:

- Truncate/regenerate over a window containing a settled / historical / account-anchor / rule-anchor
  period -> raises the lock; assert the DB is byte-for-byte unchanged (period count, the settled
  txn, the anchor) -- never a partial delete.
- Truncate/regenerate that would discard hand-entered/override rows without `confirm_discard` ->
  raises `PayPeriodDiscardRequired`; nothing deleted.
- Deleting an anchor period by any path -> IntegrityError (the Phase 0 FK), never a silent NULL.
- Concurrency: two top-ups (and a top-up racing a manual extend/regenerate) against the same short
  state -> the unique index guarantees `COUNT(*) == COUNT(DISTINCT period_index)`; the loser no-ops
  or fails loudly, never lands a duplicate index.
- IDOR: a user cannot extend/truncate/regenerate/reset another user's schedule (404, the project
  security response), and a companion cannot reach any of these routes (`@require_owner`).
- Resolver fuzz: after a randomized sequence of extend/truncate/regenerate operations, the invariant
  checker (Discipline 1) and a hand-anchored balance spot-check (Discipline 2) both still hold --
  the strongest guard that no operation ordering corrupts the index->calendar->balance chain.

Run via `./scripts/test.sh`; every batch ends `<N> passed`. The full suite is the final gate, and a
green suite is the definition of done for each phase precisely because it is the only safeguard.

## Verification (end-to-end)

1. `python scripts/build_test_template.py` after Migration 2; run targeted then full suite via
   `./scripts/test.sh`.
2. `flask db upgrade` then `flask db downgrade` for ALL migrations -- Migration 1 (FK), 1b (unique
   index), and 2 (`pay_schedule`) -- in both directions (DoD requires both directions).
3. `pylint app/ --fail-on=E,F,shekel-decimal-from-float,shekel-refname-compare,shekel-bare-money-quantize,shekel-disable-rationale`
   clean.
4. Manual in the dev container (`docker compose -f docker-compose.dev.yml up -d`): generate a
   schedule; extend by N and confirm new periods show recurring rows in `/grid`; truncate and
   confirm the settled/anchor lock blocks it; regenerate a future tail; enable rolling (target 52),
   reload `/grid`, confirm the window tops up to 52.

## Critical files

- `app/services/pay_period_service.py` (reused: `generate_pay_periods` 70-147,
  `_reject_overlapping_batch` 26-67, `get_all_periods` 197)
- `app/services/recurrence_engine.py` (reused: `generate_for_template` 131),
  `app/services/transfer_recurrence.py` (reused: `generate_for_template` 46),
  `app/services/_recurrence_common.py` (reused: `should_skip_period` 181),
  `app/services/scenario_resolver.py` (reused: `get_baseline_scenario`)
- `app/services/anchor_service.py` (reused: `apply_anchor_true_up` 166-258, Phase 3),
  `app/services/account_service.py` (reused: `_resolve_anchor_period_id` 48-87, Phase 3)
- `app/routes/pay_periods.py`, `app/routes/grid.py` (`index` 484-588 + trigger),
  `app/routes/settings.py` (69, 114)
- `app/models/pay_period.py` (Migration 1b unique index; the `transactions` relationship
  `pay_period.py:30` is WHY truncate must bulk-DELETE), `app/models/pay_schedule.py` (new),
  `app/models/account.py` (73-86 FK + comment, Phase 0), `app/audit_infrastructure.py`
- `migrations/versions/b4b588a49a0c_*` (C-43 helpers to reuse; extend to emit
  `DEFERRABLE INITIALLY IMMEDIATE`), `app/schemas/validation/pay_periods.py`
- `app/templates/settings/_pay_periods.html` (+ new `_pay_periods_manage.html`)
- DRY follow-up (out of scope): `app/routes/_transfer_creation_helpers.py:217`,
  `templates.py:324-331`, `app/routes/investment.py`, `app/routes/loan/payment_transfer.py` --
  candidates to later delegate to `populate_periods_from_active_templates`
