> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Financial-Calculation Audit -- Remediation Follow-up Work

Tracks structural improvements identified during the audit's remediation
commits that are deliberately out of scope for the remediation plan
itself.  Each entry is a self-contained refactor that improves
maintainability without changing financial behavior.  Add work here when
it surfaces; pick from here after Commit 37 (the remediation final
gate) closes.

Cross-references:

- Remediation plan: `remediation_plan.md`
- Commit prompts: `remediation_commit_prompts.md`
- Findings register: `08_findings.md`

---

## F-1. Split `app/routes/accounts.py` into per-domain blueprint modules

- **Surfaced during:** Commit 7 (`fix(accounts): route /accounts checking
  detail through canonical producer (E-25)`), commit `6c09ae8`.
- **Status:** resolved by Commit 21 of `remediation_follow_up_plan.md`
  (`refactor(routes): split accounts.py into per-sub-domain modules (F-1)`).

### Problem

`app/routes/accounts.py` is 1,499 lines and breaks the project's 1,000-
line per-module ceiling (pylint `C0302 too-many-lines`, pre-existing
warning).  The file holds 21 endpoints spanning five distinguishable
sub-domains separated by banner comments today, which makes the file
hard to read end-to-end and creates merge-conflict surface for every
audit commit that touches it.

### Current sub-domains (banner-delimited in the file)

| Sub-domain | Routes | Lines (approx) | Templates | External coupling |
|---|---|---|---|---|
| Account CRUD | `list_accounts`, `new_account`, `create_account`, `edit_account`, `update_account`, `archive_account`, `unarchive_account`, `hard_delete_account` | ~525 | `accounts/list.html`, `accounts/form.html` | Heavy: `account_service`, `transfer_service`, `pay_period_service`, optimistic-lock contract, the `_validate_update_account` helper, the `_account_type_is_visible` helper |
| Inline anchor edit (list) | `inline_anchor_update`, `inline_anchor_form`, `inline_anchor_display` | ~145 | `accounts/_anchor_cell.html` | Shares anchor-history idempotency machinery with grid `true_up` (F-103 / C-22) |
| Account Type CRUD | `create_account_type`, `update_account_type`, `delete_account_type` | ~160 | `settings/*` | Independent (C-28 multi-tenant guard only); does not touch `Account` rows |
| Anchor true-up (grid) | `true_up`, `anchor_form`, `anchor_display` | ~180 | `grid/_anchor_edit.html` | Shares the same idempotency helper and history-row machinery as `inline_anchor_update`; HX-Trigger to `balanceChanged` |
| Detail pages | `interest_detail`, `update_interest_params`, `checking_detail` | ~255 | `accounts/checking_detail.html`, `accounts/interest_detail.html` | Now routed through `balance_resolver` (Commit 7); Commits 8-10 touch sibling detail surfaces |

### Shared module state to re-home

- `_ANCHOR_HISTORY_UNIQUE_INDEX` constant (the F-103 / C-22 idempotency
  backstop), referenced by both anchor-update endpoints.
- `_visible_account_types`, `_owned_account_type`,
  `_validate_update_account`, `_account_type_is_visible` helpers (the
  C-28 multi-tenant ownership machinery).
- Six Marshmallow schema singletons (`_anchor_schema`, `_create_schema`,
  `_update_schema`, `_type_create_schema`, `_type_update_schema`,
  `_interest_params_schema`).
- The `accounts_bp` blueprint -- all 21 routes register against it.

### Two split options

- **Option A (single blueprint, file split by import).** Estimated
  effort: 1-2 days.  Keep one `accounts_bp` blueprint; split routes
  across `app/routes/accounts/__init__.py` (registers the blueprint),
  `accounts/crud.py`, `accounts/anchor.py`, `accounts/types.py`,
  `accounts/detail.py`.  Each file does `from . import accounts_bp` and
  registers its own decorators.  URLs unchanged; no `url_for` call
  sites edited.  This is the recommended option.
- **Option B (per-sub-domain blueprints with new `url_prefix`s).**
  Estimated effort: 3-4 days.  Adds the cost of updating every
  `url_for("accounts.X")` reference in routes, templates, JS, and
  tests.  ~50 references in routes, ~100-150 in templates and tests.
  High blast radius, all mechanical, but no organisational gain over
  Option A.

### Effort breakdown (Option A)

- 4-6h: factor the shared anchor-history idempotency helper (the
  `IntegrityError + uq_anchor_history_account_period_balance_day`
  handler that lives inline in both `inline_anchor_update` and
  `true_up`) into one named helper in `app/services/entry_service.py`
  or a new `app/services/anchor_service.py`.  The DRY win lands here;
  can be verified before any file moves.
- 2-3h: move helpers and schema singletons into
  `app/utils/account_validation.py` (or similar) and update imports.
- 3-4h: physically split into the per-sub-domain files.
- 1-2h: pylint, run the full suite, fix any forgotten internal
  references (e.g. `app.routes.accounts.<symbol>` test imports).

### Why defer until after Commit 37

1. The split is mostly mechanical movement, but every remaining audit
   commit that touches this file would have to be rebased through the
   split if it lands first -- doubling the merge-conflict surface for
   limited benefit.
2. The first step of the split (extracting the shared anchor-history
   idempotency helper) is a finding-adjacent DRY win and could be
   folded into Commit 16 (loan principal true-up) where the same
   shape is implemented; landing the highest-value extraction without
   taking on the full file split.
3. After Commit 37 the split becomes a single self-contained refactor
   PR with no audit-fix interaction.

### Remaining remediation commits that still touch this file

Quoted here so the split planner knows what conflicts with what:

- Commit 8 -- does NOT touch this file (year-end / net-worth /
  investment / retirement live in their own modules).
- Commit 11 -- test-only; consumes `/accounts/<id>/checking` through
  the test client, no source edit here.
- Commit 15 -- demotes loan param columns; touches loan consumers in
  `app/routes/loan.py`, not this file (no cross-leakage confirmed).
- Commit 16 -- loan principal edit as a dated true-up; implementation
  in `loan.py`.  The existing checking-anchor true-up machinery in
  this file is the reference implementation Commit 16 reads.
- Commit 21 -- semantic `is_settled` hard-delete guard.  Touches
  `archive_helpers` and the hard-delete path; the `hard_delete_account`
  route here is a consumer of those helpers and may need a touch-up.
- Commit 24 -- Marshmallow / DB CHECK reconciliation.  The schema
  singletons here would be re-read against the constraint changes.

### Acceptance criteria for the eventual split PR

- `pylint app/routes/accounts*` shows no `C0302` warnings.
- Every `url_for("accounts.X")` reference still resolves (Option A:
  trivially true; Option B: requires audit of all references).
- The full pytest suite passes with no test edits.
- The F-103 / C-22 anchor-history idempotency handler lives in exactly
  one place (the DRY violation that motivated the split is resolved).

---

## F-2. Remove dead anchor-None branches in `calendar_service`

- **Surfaced during:** Commit 9 (`fix(calendar): month-end balance via
  canonical balance-as-of-date (E-27)`), commit `9871bf7`.
- **Status:** resolved by Commit 11 of the follow-up plan.
  ``app/services/calendar_service.py`` now defines
  ``CalendarAccountNotResolvableError(LookupError)`` and raises it from
  ``get_month_detail`` / ``get_year_overview`` when
  ``resolve_analytics_account`` or ``get_baseline_scenario`` returns
  ``None``.  ``_empty_month`` and ``_empty_year`` are deleted (no
  remaining caller).  ``app/routes/analytics.py::calendar_tab`` catches
  the exception around the CSV branch and the HTMX render branch and
  ``abort(404)``s, matching the project security rule "404 for both
  'not found' and 'not yours'".  Pinned by
  ``TestUnresolvableAccountOrScenario`` (service level) and the
  ``test_calendar_tab_404_*`` / ``test_calendar_tab_200_when_resolvable``
  cases in ``tests/test_routes/test_analytics.py::TestCalendarTab``.

### Problem

Three branches in `app/services/calendar_service.py` short-circuit on
the pre-E-19 NULL-anchor state and return zeroed
`MonthSummary` / `YearOverview` objects:

- `get_month_detail` (`:115-120`) -- `if account is None: return
  _empty_month(...)` and `if scenario is None: return
  _empty_month(...)`.
- `get_year_overview` (`:156-162`) -- the matching pair returning
  `_empty_year(year)`.
- `_empty_month` (`:482`) -- builds the zeroed summary used by both
  routes, including a hardcoded `projected_end_balance=Decimal("0")`
  (the "D6-02 fifth anchor-None behavior" the audit named at
  `08_findings.md:751-753`).

After Commit 3 (`fix(anchor): backfill origination anchor`) plus
Commit 4 (`feat(balance): date-anchored anchor resolver`), every
account row has a resolvable anchor and `resolve_anchor` either
returns a valid `AnchorPoint` or raises `RuntimeError`.  The
`account is None` / `scenario is None` paths can only fire when the
upstream resolvers (`resolve_analytics_account`,
`get_baseline_scenario`) themselves return `None`, which would
indicate a separate ownership / scenario-setup defect.  Treating
that case as a silently-zeroed calendar masks the upstream bug; the
zeroed `MonthSummary` ships a `Decimal("0")` calendar to the user
with no error.

### Recommended direction

The remediation plan's locked answer (E-19, Q-16/A-16) is "one anchor
resolver, NULL anchor unreachable."  The calendar should adopt the
same contract: `resolve_analytics_account` raises (or the route
short-circuits with a 404) when the account is gone; missing
scenario likewise.  Concretely:

1. Convert `get_month_detail` / `get_year_overview` to raise a
   specific exception (e.g. `AccountNotResolvableError`) when the
   account / scenario resolvers return `None`.  Routes catch the
   exception and surface a 404 -- matching the project's "404 for
   both 'not found' and 'not yours'" security rule.
2. Delete `_empty_month` and `_empty_year` (no remaining caller).
3. Update tests: any test that relied on the implicit zeroed return
   becomes an explicit 404 assertion or sets up a valid account /
   scenario.

### Why defer

The plan's "anchor-None family" cleanup (D6-02 -> CRIT-01) was
scoped to the balance producers (Commits 3-8) and the calendar
month-end (Commit 9 just landed).  The per-route surfacing of the
unreachable state -- including the calendar's `_empty_*` factories
plus any sibling unreachable-state branches in
`savings_dashboard_service`, `dashboard_service`,
`year_end_summary_service`, etc. -- is a separate sweep, best done
in one PR after Commit 37 so the audit-fix commits and the dead-code
removal do not interleave.

### Acceptance criteria for the eventual PR

- `git grep -n "if.*is None.*return.*_empty_" app/services/` shows
  no matches.
- `git grep -nF 'return Decimal("0")' app/services/calendar_service.py`
  shows no matches (the hardcoded zero is gone with `_empty_month`).
- Calendar / year-overview routes return 404 for an unresolvable
  account or scenario; targeted route tests assert the 404.
- Full pytest suite passes.

---

## F-3. Calendar per-day filter: all-status vs grid Projected-only (W-065)

- **Surfaced during:** Commit 9 (`fix(calendar): month-end balance via
  canonical balance-as-of-date (E-27)`), commit `9871bf7`.
- **Status:** resolved by Commit 10 of the follow-up plan.
  ``_query_transactions_for_range`` now applies
  ``balance_contributing_clause()`` in its SQL filter and
  ``_assign_transactions_to_days`` re-applies
  ``is_balance_contributing(txn)`` in the Python loop, locking the
  Choice-2 semantic (Projected + Settled, excludes Credit and
  Cancelled).  Calendar day cells intentionally diverge from the
  grid's Projected-only period subtotal.  Pinned by the
  ``TestBalanceContributingPredicate`` C10-1 through C10-4 tests in
  ``tests/test_services/test_calendar_service.py``.

### Problem

`app/services/calendar_service.py::_assign_transactions_to_days`
classifies and totals every non-deleted transaction returned by
`_query_transactions_for_range`, with NO Projected-only gate.  The
grid, by contrast, sums only Projected items into its per-period
subtotal and excludes Settled / Cancelled / Credit rows from the
balance-contributing set (E-15 / Commit 2's
`balance_contributing_clause`).  The audit named this drift in
HIGH-02 / W-065:

> "per-day totals use all-status vs grid Projected-only (W-065
> DEFINITION_DRIFT, F-004 cross-ref)" -- `08_findings.md:731`,
> `03_consistency.md:6044`

Worked example: on a single calendar day with one Projected $500
expense and one Settled $500 actual paid on the same date, the
calendar day-cell shows $1,000 of expenses while the grid's
period subtotal counts $500.  Same data, two surfaces, different
totals.  Commit 9 fixed the month-end *balance* (E-27) but left
the per-day classification untouched -- the day-cell display path
was explicitly out of scope.

The relevant code:

- `_assign_transactions_to_days` (`:270-310`) -- no status gate;
  every transaction with a `due_date` in the range hits the
  `day_map` and the `total_income` / `total_expenses` totals.
- `_query_transactions_for_range` (`:194-237`) -- the query also
  returns all-status (only `is_deleted=False` is enforced); a
  status filter would need to land here too to keep the SQL
  predicate aligned with the Python summation predicate (the same
  "SQL and Python must agree" principle that motivated
  `balance_contributing_clause` in Commit 2).

### Recommended direction

Apply the locked E-15 / E-25 predicate to the calendar per-day
classification so the same rule that governs grid subtotals,
`/savings`, `/accounts`, and the dashboard governs calendar day
cells too.  Two design choices remain for the developer to confirm:

- **Choice 1: Projected-only (matches grid).**  Settled / Received
  rows do not appear on calendar day cells.  Loses visibility of
  realized payments at the calendar surface.
- **Choice 2: balance-contributing (Projected + Settled, excludes
  Credit / Cancelled).**  Settled rows appear on the calendar at
  their settled date; Credit / Cancelled hidden.  More aligned
  with what a user expects of a "calendar of activity," but the
  per-day totals will then differ from the grid's
  Projected-only subtotal.

Either choice resolves the W-065 drift; the developer must pick
which surface defines the canon.  The plan's locked E-25 names
the balance producer's Projected-only predicate; the calendar's
per-day *display* may legitimately want a wider filter
(Choice 2), but the choice must be made explicit and documented.

### Implementation sketch

1. Reuse `app.utils.balance_predicates.balance_contributing_clause`
   (Commit 2) in `_query_transactions_for_range` for the SQL
   filter.
2. Re-apply the same predicate in `_assign_transactions_to_days`
   (Python loop) so SQL + Python agree.
3. Tests: pin per-day totals against hand-computed values for a
   fixture with mixed Projected / Settled / Cancelled / Credit
   rows; assert the calendar day-total equals the grid
   period-subtotal (or the documented Choice-2 difference, if the
   developer picks Choice 2).
4. Update `_calendar_month.html` Jinja template if the chosen
   filter changes which entries reach the template (otherwise
   no template change).

### Why defer

W-065 is a definition-drift issue on a display surface; no
developer-reported wrong dollar is bound to it (HIGH-02's
displayed-wrong-dollar evidence is the balance facet, fixed by
Commit 9).  It requires a product decision (Choice 1 vs Choice 2)
which is not on the locked E-NN list, so it should not be folded
into the audit-fix commit chain.

### Acceptance criteria for the eventual PR

- Calendar per-day totals are computed with a documented,
  named status predicate (no inline status-filter logic in
  `_assign_transactions_to_days`).
- The chosen predicate is locked by a test that fails if the
  filter regresses (e.g. a Settled row toggled to Projected
  changes the day total).
- `git grep -nF 'is_deleted.is_(False)' app/services/calendar_service.py`
  shows the calendar query reuses the shared predicate, not an
  inline filter.
- Full pytest suite passes; calendar route tests still green.

---

## F-4. Plan drift: Commit 10's obligations cross-reference is stale

- **Surfaced during:** Commit 10 (`fix(grid): period_subtotal through
  canonical producer (Q-10, E-25)`).
- **Status:** resolved by Commit 1 of `remediation_follow_up_plan.md`,
  doc-only correction. The Commit 10 "Files modified" list no longer
  names `app/routes/obligations.py`, the now-orphaned C10-4 test row
  has been dropped, and a clarifying trailing note points readers to
  Commit 23 (E-24 / HIGH-05) for the obligations monthly-equivalent
  aggregator. The body below is preserved for traceability.

### Problem

`remediation_plan.md` Section 9 Commit 10 names
`app/routes/obligations.py` as a target for routing through
`balance_resolver.period_subtotal`:

> `app/routes/obligations.py`: the manual subtotal at ~`:331-408`
> also routed through it (same concept).

The current obligations route does NOT compute a per-period
transaction subtotal.  It aggregates `amount_to_monthly(...)`
across `TransactionTemplate` / `TransferTemplate` rows (a monthly-
equivalent rollup, with no `(account, scenario_id, period)`
parameter set).  `period_subtotal` is the wrong API for that
operation: forcing it in would require throwing away the
template-level recurrence math and re-deriving it from the
generated transactions, with no semantic gain.

The audit's own structure already separates the two: F-004
(`period_subtotal`) explicitly cross-links Q-12 for the
obligations-path subtotal and calls it "out of P3-a balance scope;
cross-link only" (`03_consistency.md:405-407`).  The obligations
monthly aggregator is Q-12 / E-24 / Commit 23 territory.

### Recommended direction

Treat the Commit 10 line as a doc-only drift that Commit 23 will
naturally cover.  No code change is needed to close this entry.

### Acceptance criteria for the eventual doc PR

- `remediation_plan.md` Commit 10 section is corrected to drop the
  obligations bullet, or Commit 23's section is updated to
  explicitly reference E-24 instead of period_subtotal.
- No code under `app/routes/obligations.py` is changed by Commit 10.

### Why defer

The plan's prose is wrong but the actual remediation pipeline
already routes the work correctly through Commit 23.  Closing
this entry is a single-paragraph doc edit best folded into the
final remediation pass (Commit 35 / 37) where other doc-drift
corrections are batched.

---

## F-5. Dead `_skip_user_bootstrap_period` global flag in `tests/conftest.py`

- **Surfaced during:** Commit 11 (`test(integration): cross-page balance-
  equality regression lock (HIGH-01)`), commit `4674e7e`.
- **Status:** resolved by Commit 2 of `remediation_follow_up_plan.md`.
  The `global _skip_user_bootstrap_period` declaration and the two
  assignments (`= True` before flush, `= False` in `finally`) have been
  removed from the `bare_user` fixture, along with the inline comment
  describing the (non-existent) listener.  Project-wide grep across
  `app/` and `tests/` returns empty.  The body below is preserved for
  traceability.

### Problem

`tests/conftest.py:844` declares `global _skip_user_bootstrap_period`
and the `bare_user` fixture toggles it (`:853` -> True, `:857` -> False)
around its `db.session.flush()`, but a project-wide grep
(`grep -rn '_skip_user_bootstrap_period' /home/josh/projects/Shekel/`)
returns matches only inside that one fixture; no `event.listens_for`
or other listener consumes the flag.  Reading the flag's role: it
was intended to suppress an `after_insert` listener on `User` that
inserts a bootstrap pay period via `auth_service.create_user`-style
machinery, so `bare_user` could yield a truly bare user (no period,
no account anchor) for `pay_period_service` tests.  The bootstrap
period is now inserted inline in the `bare_user` body itself rather
than through a listener, so the flag has no reader.

### Recommended direction

Delete the `global` declaration and both assignments; verify the
`bare_user` fixture still yields the bare-user-with-no-period state
the dependent tests expect.

### Why defer

Dead code in a test fixture; zero functional impact.  Out of scope
for HIGH-01 (no financial-calculation correctness signal), and the
removal needs a targeted run of every test that consumes `bare_user`
to confirm no implicit dependency.

---

## F-6. Cross-page balance lock readers could parse rendered HTML directly

- **Surfaced during:** Commit 11 (`test(integration): cross-page balance-
  equality regression lock (HIGH-01)`), commit `4674e7e`.
- **Status:** resolved by Commit 20 of `remediation_follow_up_plan.md`
  (`test(routes): static guard against bypass of balance_resolver in
  grid/accounts (F-6)`).  Static greps now live in
  `tests/test_routes/test_grid.py::TestGridPeriodSubtotalCanonical::test_grid_balance_computation_routed_through_resolver`
  and
  `tests/test_routes/test_accounts.py::TestCheckingDetailCanonicalProducer::test_accounts_checking_balance_routed_through_resolver`.

### Problem

In `tests/test_integration/test_cross_page_balance_equality.py`, the
grid and /accounts-checking surface readers (`_grid_value`,
`_accounts_checking_value`) replicate the
`balance_resolver.balances_for(...)` call that the routes make
internally, rather than driving the route via `auth_client.get(...)`
and parsing the rendered HTML for the displayed Decimal.  Both
routes are exercised at the route level for status 200, but the
Decimal extraction itself goes through the producer, so a
hypothetical regression that bypasses `balance_resolver` in the
route handler (e.g. someone re-introduces a hand-rolled balance
loop in `app/routes/grid.py` and forgets to delete the
`balance_resolver.balances_for(...)` call that the test reader
re-runs) would not be caught by the equality assertion.

The four other surface readers (dashboard, /savings, year-end,
calendar) DO call the surface's public service function, so they
catch divergence between the route's exposed value and the
canonical producer.  The grid and /accounts paths are the
remaining gap.

### Recommended direction

Two options, in order of cost:

- **(a) Static lock on the route source** (cheap).  Add a grep-
  style guard in `tests/test_routes/test_grid.py` and
  `tests/test_routes/test_accounts.py` that fails when the route's
  balance-computation block contains anything other than a
  `balance_resolver.balances_for(...)` call -- modeled on the
  existing `test_grid_inline_subtotal_loop_removed`
  (`tests/test_routes/test_grid.py:3794`).
- **(b) HTML parsing in the cross-page readers** (more authentic
  but fragile).  Replace `_grid_value` and `_accounts_checking_value`
  with HTML scans of the route response: grid renders
  `${:,.0f}` in `app/templates/grid/_balance_row.html:26`; the
  checking-detail template renders the same.  HTML parsing across
  these specific templates needs a robust regex anchored to the
  enclosing class (`.balance-row-summary`, the period cell index)
  to avoid false matches.

### Why defer

The current readers achieve the HIGH-01 lock's stated goal -- catch
math-layer divergence (CRIT-01 / F-009 / symptom #1, #5) -- and the
route plumbing is guarded by the status-200 assertion plus the
existing `test_grid_inline_subtotal_loop_removed` static lock.  A
hypothetical route-handler bypass of `balance_resolver` is a
narrower regression than the math-layer one HIGH-01 closes, and
strengthening the readers is gold-plating relative to that scope.
Option (a) above is the lower-risk follow-up if a real regression
in this gap surfaces.

### Acceptance criteria for the eventual PR

- Either the grid and /accounts-checking route handlers carry a
  static-grep guard equivalent to
  `test_grid_inline_subtotal_loop_removed`, OR
- `_grid_value` and `_accounts_checking_value` parse the rendered
  HTML for the balance Decimal and assert the test still bites
  for the symptom-tuple data.
- Full pytest suite passes.

---

## F-7. Jinja-globals ID exposure list duplicated between `app/__init__.py` and `tests/conftest.py`

- **Surfaced during:** Commit 11 (`test(integration): cross-page balance-
  equality regression lock (HIGH-01)`), commit `4674e7e`.  The
  duplication itself predates this commit and carries an inline
  "out of scope for C-28" rationale (unrelated to the financial-
  calculations audit C-28 is a different remediation effort and
  does not own this debt going forward).
- **Status:** resolved by Commit 6 of the follow-up plan
  (`refactor(tests): extract Jinja-globals registration helper +
  sync missing entries (F-7)`).  Plan-time verification additionally
  showed the conftest list was missing eight entries
  (`TIMING_PRE_TAX`, `TIMING_POST_TAX`, `CALC_PERCENTAGE`,
  `CALC_FLAT`, `GOAL_MODE_FIXED`, `GOAL_MODE_INCOME_RELATIVE`,
  `INCOME_UNIT_PAYCHECKS`, `INCOME_UNIT_MONTHS`); the extraction
  closed both the DRY violation and the drift in one commit.  The
  rest of this entry is kept for historical traceability of the
  pre-fix state.

### Problem

The ID-derived Jinja global registration list lived in two places
that were intended to stay byte-identical:

- `app/__init__.py:168-219` -- 40+ `app.jinja_env.globals[...] =
  ref_cache.<lookup>(...)` lines inside the `create_app` factory.
- `tests/conftest.py:2086-2124` -- the same 40+ lines inside
  `_refresh_ref_cache_and_jinja_globals`, which the `db` fixture
  invokes once per test to re-seat the cache after the per-test
  drop+reclone (Phase 3b).

A missing entry on the test-side breaks every template that
references the omitted constant at request time, with a confusing
Jinja `UndefinedError`; a missing entry on the production side
breaks the same templates in dev / prod.  The current inline
docstring in `_refresh_ref_cache_and_jinja_globals` justifies the
duplication ("`app/__init__.py` runs inside `create_app()` which
is called once per test session, while this helper runs once per
test"), but the project's DRY/SOLID rule
(`feedback_dry_solid_normalized` in user-memory) flags any
two-source-of-truth pattern as a regression risk.

### Recommended direction

Two options, in order of cost:

- **(a) Extract to a shared function** (cheap).  Move the list of
  `(name, lookup_callable)` pairs (or a single function that takes
  an `app` and registers them) into a new helper module, e.g.
  `app/jinja_globals.py`, and have both `create_app` and the
  conftest helper import and call it.  Single source of truth;
  the per-test re-seat still happens but reads from the same list.
- **(b) Refactor `create_app` so the registration is callable
  standalone** (more involved).  Extract a
  `register_ref_id_globals(app)` function inside `app/__init__.py`
  that both `create_app` and the conftest helper call; the
  enums import already pulls from `app.enums`.  Same DRY outcome,
  slightly different layout.

Either way, the acceptance criterion is that adding a new
``ref.<table>`` enum requires editing exactly one list.

### Why defer

The duplication is a quality / DRY violation, not a correctness
bug -- both lists are currently in sync (verified this session via
side-by-side read of `app/__init__.py:169-218` and
`tests/conftest.py:2088-2124`).  A test would have caught any
drift the next time a Jinja Undefined surfaced.  Out of scope for
HIGH-01 (which targets balance-producer correctness, not Jinja
plumbing); best folded into a focused infrastructure PR after
Commit 37 alongside any other test-harness DRY items.

### Acceptance criteria for the eventual PR

- `git grep -n "STATUS_PROJECTED.*ref_cache.status_id" app/ tests/`
  shows exactly one match (the canonical registration site).
- Both `create_app` and the conftest's
  `_refresh_ref_cache_and_jinja_globals` route through the shared
  helper.
- Full pytest suite passes; in particular, the dev server still
  renders templates that reference any of the 40+ constants
  without raising `UndefinedError`.

---

## F-8. Engine anchor-reset clobbers the contractual payment for fixed-rate loans

- **Surfaced during:** Commit 13 (`feat(loan): pure event-derived loan
  resolver (E-18)`), this commit.
- **Status:** resolved by Commit 14 of `remediation_follow_up_plan.md`
  (`fix(engine): gate anchor-reset payment recompute on not
  using_contractual (F-8)`).  The anchor-reset block in
  ``amortization_engine.generate_schedule`` now skips the
  ``calculate_monthly_payment`` recompute when ``using_contractual`` is
  true, preserving the contractual P&I for fixed-rate loans through
  the anchor.

### Problem

`app/services/amortization_engine.generate_schedule` honors the
``anchor_balance`` / ``anchor_date`` parameters by snapping the
running balance and unconditionally re-computing
``monthly_payment`` at the snap:

```python
if (anchor_balance is not None and anchor_date is not None
        and not anchor_applied and pay_date > anchor_date):
    balance = anchor_balance
    anchor_applied = True
    months_left = max_months - month_num + 1
    monthly_payment = calculate_monthly_payment(
        balance, current_annual_rate, months_left,
    )
```

This is correct for ARM loans (``using_contractual`` is False, so
``max_months = remaining_months``), where re-amortization at the
anchor is the intended behavior.

For fixed-rate loans (``using_contractual`` is True), the recompute
is structurally wrong: ``max_months = remaining_months + term_months``
(a generous upper bound for the early-payoff case), so
``months_left`` at the anchor reset is roughly ``2 * term_months``,
which drives ``calculate_monthly_payment`` to produce a payment
about half the correct contractual amount.  Every subsequent row in
the schedule then uses that wrong payment, and the projected
balances drift accordingly.

### Why this is unreachable today

Every production call site that passes ``anchor_balance`` /
``anchor_date`` (Commit-13 resolver included) explicitly skips the
anchor when the loan is fixed-rate -- see
`app/routes/loan.py:476-489` (``floor_anchor_bal``) and the
analogous "is_arm guards" elsewhere.  Commit 13's resolver mirrors
the same pattern by passing ``anchor_balance=None`` for fixed-rate
loans, so the buggy branch stays dormant.

The gap surfaces only when a user records a dated balance trueup
(D-C, Commit 16) on a fixed-rate loan whose anchor balance diverges
from the engine's from-origination projection -- e.g. a borrower
who prepaid principal.  In that case:

- The resolver-derived ``current_balance`` is correct because
  ``_replay_balance_from_anchor`` operates on the primary data
  (anchor + confirmed payments) without depending on the engine.
- The resolver-derived ``monthly_payment`` is correct because
  the fixed-rate branch returns the contractual payment
  unconditionally.
- The resolver-returned ``schedule`` (for display) would have
  post-anchor rows projected from the wrong payment if the
  resolver passed the anchor to the engine.

Commit 13 avoids the bug by passing ``anchor_balance=None`` for
fixed-rate, so post-anchor rows for the fixed-rate trueup case use
the from-origination projection (slightly off from reality in
trueup-corrected balance terms, but correct in P&I-split terms).

### Fix

Gate the payment recompute on ``not using_contractual``:

```python
if (anchor_balance is not None and anchor_date is not None
        and not anchor_applied and pay_date > anchor_date):
    balance = anchor_balance
    anchor_applied = True
    if not using_contractual:
        months_left = max_months - month_num + 1
        monthly_payment = calculate_monthly_payment(
            balance, current_annual_rate, months_left,
        )
```

This is a two-line targeted change with full backward
compatibility for current callers (none of which exercise the
buggy combination) and no behavior change for ARM loans.  After
the fix, the resolver can pass the anchor universally and the
schedule's post-trueup rows for fixed-rate loans will project
from the correct balance with the correct payment.

### Acceptance criteria for the eventual PR

- `app/services/amortization_engine.py`: anchor reset gated on
  ``not using_contractual`` (or equivalent).
- A new engine test: with anchor at origination,
  ``original_principal`` set, no rate changes, the produced
  ``monthly_payment`` for every row equals
  ``calculate_monthly_payment(original_principal, rate, term)``.
- The Commit 13 resolver gains a fixed-rate trueup pathway:
  ``engine_anchor_balance`` / ``engine_anchor_date`` are set for
  both ARM and fixed-rate.
- A new resolver test: fixed-rate loan with mid-loan user_trueup
  whose balance diverges from the engine's projection; the
  schedule's post-trueup rows project from the trueup balance,
  not from the from-origination projection.

---

## F-9. No origination LoanAnchorEvent on new-loan creation

- **Surfaced during:** Commit 14 (`test(loan): settled transfer reduces
  resolved principal (symptom #3)`), this commit.
- **Status:** **resolved by Commit 15.** `app/routes/loan.py:create_params`
  now writes the origination :class:`LoanAnchorEvent` inline after the
  :class:`LoanParams` insert, mirroring `account_service.create_account`'s
  paired-row pattern.  Test fixtures across `tests/test_routes/`,
  `tests/test_services/`, and `tests/test_integration/` were updated
  to use `tests._test_helpers.insert_origination_event` after their
  direct :class:`LoanParams` constructions.  Centralisation into a
  `loan_service.create_loan` helper (option 2 in the original
  direction) remains a possible future cleanup if more callers
  appear; it is not required since the only production write site
  is now compliant.

### Problem

Commit 12 (`feat(loan): append-only loan_anchor_events table +
backfill (E-18)`) inserts an origination event for every
:class:`LoanParams` row that exists at migration time.  No
application code path creates an origination event when a new loan
is created post-migration:

- `app/services/account_service.create_account` is generic across
  account types and does not know about :class:`LoanAnchorEvent`.
- `app/routes/loan.py` has no shared "create-loan" service; the
  loan dashboard setup form writes :class:`LoanParams` directly and
  does not append an event.
- The Commit-12 migration backfill ran once against the existing
  data set and is not re-run on new inserts.

The Commit-13 resolver explicitly requires a non-empty event list
(``_select_latest_anchor`` raises ``ValueError`` when ``anchor_events``
is empty, citing "Commit 12 backfill should have produced an
origination event for every loan").  Once Commit 15 routes consumers
through the resolver, the loan card / debt strategy / net-worth
liability for a freshly created loan will raise instead of rendering.

Verified by ``grep -rn "LoanAnchorEvent" app/routes/ app/services/``
(empty -- only the resolver module references the class) and by the
integration tests in
``tests/test_integration/test_loan_principal_settles.py`` which
must create the origination event explicitly via the helper
``_create_mortgage`` (a production code path would need to do the
same work).

### Recommended direction

Two options:

- **Inline at the existing :class:`LoanParams` insert sites**: each
  route or service that creates a :class:`LoanParams` row also
  inserts an origination :class:`LoanAnchorEvent` in the same
  transaction.  Low touch; matches the pattern of "create the
  paired row inline" used elsewhere.
- **Centralize via a new ``loan_service.create_loan`` helper**: one
  service-level entry point creates :class:`LoanParams` plus the
  origination event together (mirrors
  :func:`account_service.create_account` which writes the
  :class:`Account` plus the origination
  :class:`AccountAnchorHistory` row).  Higher up-front cost,
  better DRY when more callers appear.

The second option also slots cleanly into Commit 16 if the same
service ends up owning user-trueup appends (already extracted into
``anchor_service.apply_anchor_true_up`` per R-7).

### Why defer

Commit 14 is test-only by charter (test that confirmed transfers
reduce principal once the resolver is used).  Adding new-loan
creation logic would mix scopes and obscure the symptom-#3 lock
this commit installs.  Commit 15 must address the gap before it
routes the loan card through the resolver, or it must defer the
loan-card routing until this is fixed.

### Acceptance criteria for the eventual PR

- Every code path that inserts :class:`LoanParams` also inserts an
  origination :class:`LoanAnchorEvent` in the same transaction
  (``anchor_date = origination_date``,
  ``anchor_balance = original_principal``,
  ``source_id = ORIGINATION``).
- A new integration test creates a loan via the production route
  and asserts ``loan_resolver.resolve_loan`` succeeds (does not
  raise ``ValueError`` for empty anchors).
- The Commit 14 integration test helper ``_create_mortgage`` is
  updated to call the shared service path so the test exercises
  the production code path rather than duplicating it.

---

## F-10. Engine internals still read `LoanParams.current_principal` / `.interest_rate`

- **Surfaced during:** Commit 15 (`refactor(loan): demote
  current_principal/interest_rate; route all consumers (E-18)`).
- **Status:** resolved by Commit 15 of `remediation_follow_up_plan.md`
  (`refactor(loan): delete dead get_loan_projection /
  calculate_balances_with_amortization (F-10)`).  Both dead functions
  (and the ``LoanProjection`` dataclass) were deleted from
  ``app/services/amortization_engine.py`` and
  ``app/services/balance_calculator.py``; ``compute_contractual_pi``
  was rewritten to read ``original_principal`` and the base
  ``interest_rate`` (both required by the Marshmallow schema and never
  NULL in practice).  The historical state below is preserved for
  traceability.

  Three engine-side functions remained after Commit 15 of the main
  remediation routed all display paths through the resolver:

  - `app/services/amortization_engine.py:913` --
    `get_loan_projection(params, ...)` reads
    `params.current_principal` / `params.interest_rate`.  Orphaned
    in production after Commit 15 (zero callers); kept alive by
    its substantial test surface in
    `tests/test_services/test_amortization_engine.py`
    (`TestGetLoanProjection`, `test_get_loan_projection_*`, and
    several ARM tests).
  - `app/services/balance_calculator.py:226` --
    `calculate_balances_with_amortization(...)` reads
    `loan_params.current_principal`.  Orphaned in production after
    Commit 8 routed the savings dashboard through `balance_resolver`;
    kept alive by `tests/test_services/test_balance_calculator_debt.py`.
  - `app/services/loan_payment_service.py:252` --
    `compute_contractual_pi(params)` reads
    `params.current_principal` / `params.interest_rate` to compute
    the "above-P&I excess" boundary used by
    `prepare_payments_for_engine`'s escrow subtraction step.  Still
    actively called by `load_loan_context`, which the resolver
    consumes transitively for its payment feed.

### Problem

The Commit-15 verification gate is
``grep -rn "\.current_principal" app/ | grep -v migrations |
grep -v loan_resolver | grep -v loan_anchor_event`` shows ONLY
write/seed paths.  The three sites above are reads, not writes, and
they sit outside the resolver/event modules.  Commit 15's scope
(per `remediation_plan.md` Section 9 C) explicitly lists only
`routes/loan.py`, `routes/debt_strategy.py`,
`services/savings_dashboard_service.py`, and
`services/year_end_summary_service.py`; the engine internals are
out of scope.

Pragmatically these reads are no longer display contracts -- they
are either dead production code (the first two) or internal
helpers the resolver consumes (the third) -- so they cannot drive
the symptom-#5 family of bugs Commit 15 fixed.  They violate the
strict letter of the gate but not its intent.

### Recommended direction

Commit 17 (`fix(loan): unify per-period/interest/payoff figures via
resolver+round_money (HIGH-08)`) is the natural home:

- **`get_loan_projection`**: delete the function and its
  `LoanProjection` dataclass; rewrite the
  `tests/test_services/test_amortization_engine.py` tests that
  exercise it to drive `loan_resolver.resolve_loan` directly.
- **`calculate_balances_with_amortization`**: delete the function
  and the `test_balance_calculator_debt.py` test class.  Production
  no longer calls it; the canonical balance producer is
  `balance_resolver`.
- **`compute_contractual_pi`**: replace the
  `params.current_principal` read with the resolver's
  `state.current_balance` at the same call site
  (`load_loan_context`).  Currently impossible because
  `load_loan_context` is itself in the resolver's input chain
  (chicken-and-egg); the fix is to compute the escrow-boundary
  heuristic from `params.original_principal` (always available,
  never null) and the BASE rate, which gives the SAME boundary as
  today for fixed-rate loans and a slightly less precise (but
  still safe) boundary for ARMs.

### Why defer

Commit 15's charter is "route all loan consumers through the
resolver and demote the stored columns to nullable seed."  The
engine-internal reads do not break that charter -- they are not
display reads -- and rewriting them touches `amortization_engine`,
`balance_calculator`, and `loan_payment_service`, which are
explicitly out of Commit 15's Section 9 C scope.  Doing the work
here would inflate the diff substantially and mix concerns Commit
17 is purpose-built to address.

### Acceptance criteria for the eventual PR

- `grep -rn "\.current_principal" app/ | grep -v migrations | grep
  -v loan_resolver | grep -v loan_anchor_event | grep -v models/`
  is empty (no engine-internal reads remain).
- The `LoanProjection` dataclass and `get_loan_projection` function
  are deleted from `amortization_engine.py`.
- The `calculate_balances_with_amortization` function is deleted
  from `balance_calculator.py` and `test_balance_calculator_debt.py`.
- `compute_contractual_pi` computes the escrow boundary from
  `original_principal` (no `current_principal` read).
- Targeted + full pytest pass with the same green count.

---

## F-11. Truthiness `or Decimal("0")` on the per-account balance read in `compute_slider_defaults`

- **Surfaced during:** Commit 20 (`fix(retirement): zero is a value not
  missing (E-12, CRIT-04)`).
- **Status:** resolved by Commit 4 of `remediation_follow_up_plan.md`
  (`refactor(retirement): replace truthiness on financial values (F-11,
  F-12)`).  The trailing ``or Decimal("0")`` is gone; the site now uses
  an explicit ``is None`` guard.

### Problem

`app/services/retirement_dashboard_service.py:362` (post-Commit-20 line
number) reads the per-account balance for the weighted-return loop as

```python
bal = proj.get(
    "current_balance", acct.current_anchor_balance
) or Decimal("0")
```

The trailing ``or Decimal("0")`` is truthiness on a monetary value -- the
same pattern Commit 20 removed from the SWR resolver and the
weighted-return gate.  A genuine zero-balance account behaves the same
either way (the account contributes ``0 * rate = 0`` to the numerator
and ``0`` to the denominator regardless), so the line is behaviourally
inert today, but it violates the post-Commit-20 invariant
"no truthiness on financial values" in spirit and is the kind of latent
hazard that surfaces only after an upstream refactor changes what
``proj.get`` can return.

### Why defer

Commit 20's charter is "fix the two specific truthiness sites the audit
cited (CRIT-04 / F-042 / PA-04 / PA-05)."  This third site is not
called out in any finding and is behaviourally inert.  Folding it in
would expand scope and require a separate verification that
``proj.get("current_balance", ...)`` cannot in practice return ``None``
upstream (verified empirically today but not statically enforced).

### Acceptance criteria for the eventual PR

- Replace with an explicit ``is None`` guard or remove the ``or
  Decimal("0")`` once the upstream `_project_retirement_accounts`
  contract is documented as always returning a non-None Decimal.
- Add a unit-test pinning the upstream contract so the guard's removal
  is safe in perpetuity.

---

## F-12. Stylistic truthiness `if params and projection_periods:` in `_project_retirement_accounts`

- **Surfaced during:** Commit 20 (`fix(retirement): zero is a value not
  missing (E-12, CRIT-04)`).
- **Status:** resolved by Commit 4 of `remediation_follow_up_plan.md`
  (`refactor(retirement): replace truthiness on financial values (F-11,
  F-12)`).  The gate is now
  ``if params is not None and projection_periods:`` -- matches the
  post-Commit-20 convention used elsewhere in the file.

### Problem

`app/services/retirement_dashboard_service.py:444` (post-Commit-20 line
number) gates the per-account growth simulation with

```python
if params and projection_periods:
    ...
```

The first conjunct (``params``) is truthiness on a SQLAlchemy
`InvestmentParams` instance, which Python evaluates as ``is not None``
for any non-None object (no ``__bool__`` override on the class).  So
this is not a bug -- it produces the same behaviour as ``params is not
None`` -- but it is stylistically inconsistent with the post-Commit-20
``params is not None`` gate immediately above (in `compute_slider_defaults`)
and could mislead a future reader into thinking truthiness is the
project's convention here.

### Why defer

Behaviourally a no-op; the audit did not cite it.  Worth a one-line edit
in a future style-pass commit but does not justify expanding Commit 20's
diff.

### Acceptance criteria for the eventual PR

- Replace ``if params and projection_periods:`` with ``if params is not
  None and projection_periods:`` so the gate matches the post-Commit-20
  convention in the same file.
- No behaviour change; existing tests stay green.

---

## F-13. `retirement_gap_calculator` does not validate `safe_withdrawal_rate >= 0`

- **Surfaced during:** Commit 20 (`fix(retirement): zero is a value not
  missing (E-12, CRIT-04)`).
- **Status:** resolved by Commit 7 of the follow-up plan
  (`fix(retirement): reject negative SWR slider override (F-13)`).
  Decision: reject (422) via Marshmallow ``Range(min=0, max=1)`` on the
  new ``RetirementGapQuerySchema``; the calculator's permissive ``> 0``
  guard remains as defense in depth.

### Problem

`tests/test_services/test_retirement_gap_calculator.py:309-311` carries
a pre-existing in-code TODO that pre-dates Commit 20:

```
# BUG: Source does not validate SWR > 0 -- negative SWR silently
# accepted. Should raise ValidationError.
# TODO: Source should validate safe_withdrawal_rate > 0.
```

`app/services/retirement_gap_calculator.calculate_gap` guards the
division by zero (``if safe_withdrawal_rate > 0:``) but silently treats
a negative SWR the same as zero -- ``required_retirement_savings``
collapses to ``ZERO``.  Existing test
``test_safe_withdrawal_rate_negative`` pins that behaviour
intentionally.  The dashboard's `_resolve_swr_fraction` (added in
Commit 20) reads the column as-is, and the column's CHECK constraint
(`ck_user_settings_valid_safe_withdrawal`) admits ``0 <= rate <= 1``
only, so a negative value cannot reach the calculator through the
normal storage path.  The hazard is the slider-override path
(`retirement.gap_analysis` route accepts any float and divides by 100),
where a negative ``swr=`` query parameter would flow through to the
calculator.

### Why defer

Commit 20's charter is CRIT-04 (truthiness on financial values).  The
"validate or clamp negative SWR" decision is a product / UX call that
deserves its own design discussion -- the right answer might be a
Marshmallow validator on the route, a database column CHECK on the
override channel (there isn't one yet), or status quo with a comment
explaining why silent-clamp-to-zero is acceptable.

### Acceptance criteria for the eventual PR

- Decide: reject (422), clamp (silently floor to 0), or document (keep
  current behaviour and write a comment naming the constraint at the
  route layer).
- If reject: add Marshmallow `Range(min=0)` validator on the
  `swr` query parameter; update or remove the
  ``test_safe_withdrawal_rate_negative`` and matching dashboard-route
  tests.
- Remove the ``# BUG:`` / ``# TODO:`` lines in
  ``test_retirement_gap_calculator.py:309-311``.

---

## F-14. Defense-in-depth filter on `hard_delete_transfer_template` bulk delete

- **Surfaced during:** Commit 21 (`fix(templates): semantic is_settled
  hard-delete guard (E-22, CRIT-05)`).
- **Status:** resolved by Commit 8 of the follow-up plan.  The bulk-
  delete loop in ``app/routes/transfers.py::hard_delete_transfer_template``
  now filters on ``Transaction.status_id.notin_(settled_status_ids)``
  via a scalar subquery against ``Status.is_settled.is_(True)``,
  mirroring the ``templates.py::hard_delete_template`` shape added
  after CRIT-05.  Pinned by
  ``test_hard_delete_transfer_template_bulk_delete_skips_settled_rows``.

### Problem

`app/routes/transfers.py:666-673` unconditionally iterates every
linked transfer and calls
`transfer_service.delete_transfer(..., soft=False)`.  Commit 21
fixed the predicate `transfer_template_has_paid_history` to filter
on `Status.is_settled`, so the guard at `:624` now correctly catches
RECEIVED-status transfers and the destructive branch is unreachable
on the happy path.  However, the parallel route in
`app/routes/templates.py:hard_delete_template` received the
additional defense-in-depth treatment Commit 21 spec'd
(`Transaction.status_id.notin_(settled_status_ids)` on the bulk
delete) while the transfer-template route did not.  A future
regression of the predicate, a race window between the guard and
the loop, or a different caller that bypasses the guard could still
permanently destroy settled transfers and their shadow pairs.

### Why deferred

The Commit 21 plan in
`docs/audits/financial_calculations/remediation_plan.md` Section 9
explicitly scopes the defense-in-depth filter to
`hard_delete_template` (templates.py).  Extending the same pattern
to `hard_delete_transfer_template` adds value but exceeds the
plan's stated scope, and the predicate fix alone already neutralises
the active CRIT-05 data-loss path through both routes.

### Suggested implementation

Mirror the templates.py shape: build a `settled_status_ids`
scalar-subquery from `Status.is_settled.is_(True)`, partition the
linked transfers into settled vs non-settled lists, skip
`transfer_service.delete_transfer` for the settled list (surviving
transfers retain their `transfer_template_id`, which is FK ON
DELETE SET NULL, so they survive as detached settled rows when the
template is removed), and pin the behavior with a route-level test
that monkey-patches the predicate to False.

### Acceptance criteria

- `hard_delete_transfer_template`'s bulk-delete loop skips any
  transfer whose status carries `is_settled=True`.
- New route test (mirror of
  `test_hard_delete_template_bulk_delete_skips_settled_rows`)
  monkey-patches `transfer_template_has_paid_history` to False,
  posts the hard-delete, asserts the settled transfer plus its two
  shadows survive with original amounts/statuses.
- The shadow invariant test
  (`test_hard_delete_preserves_shadow_invariant`) continues to pass
  unchanged -- the filtered loop preserves invariants 1-5 for the
  rows it touches.

---

## F-15. Other ``Decimal("12")`` constants (annual-rate / month-elapsed contexts) duplicate ``MONTHS_PER_YEAR``

- **Surfaced during:** Commit 23 (`refactor(obligations): one monthly-
  equivalent aggregator (E-24, HIGH-05)`).
- **Status:** resolved by Commit 5 of `remediation_follow_up_plan.md`
  (`refactor(services): use canonical MONTHS_PER_YEAR constant
  (F-15)`).  The four service-tier sites now import and use
  ``MONTHS_PER_YEAR`` from ``app.utils.money``; the local ``TWELVE`` /
  ``MONTHS_IN_YEAR`` aliases are deleted.  A follow-up commit
  (`refactor(escrow): route bare-int /12 monthly divisions through
  MONTHS_PER_YEAR`) routed the remaining bare-``int`` ``/ 12``
  divisions in the escrow path through the same constant.

### Problem

Commit 23 collapses the biweekly-to-monthly factor (26/12) into the one
canonical ``PAY_PERIODS_PER_YEAR`` / ``MONTHS_PER_YEAR`` definition in
``app/utils/money.py``. HIGH-05 / D6-05's scope is the biweekly-to-
monthly conversion cluster, so the audit-named four sites
(``savings_goal_service.py`` once-named, twice-inlined;
``savings_dashboard_service.py`` twice-inlined;
``retirement_gap_calculator.py`` once-inlined with bare ``int``) are
all routed through the canonical constants.

Four additional ``Decimal("12")`` literals remain in
``app/services/``, all in *rate-periodicity* contexts (annual rate ->
monthly compounding, months-elapsed -> years for escrow amortization)
rather than biweekly -> monthly conversion:

```
app/services/debt_strategy_service.py:33     TWELVE = Decimal("12")
app/services/escrow_calculator.py:49         months_elapsed / Decimal("12")
app/services/interest_projection.py:45       MONTHS_IN_YEAR = Decimal("12")
app/services/loan_resolver.py:378            monthly_rate = rate_at / Decimal("12")
```

These are semantically "months per year" but in a different
application context from the biweekly-to-monthly factor (the rate
contexts only ever divide a yearly rate by 12 to get a monthly
period; they do not use 26). Strict DRY would point all four at the
shared ``MONTHS_PER_YEAR``; doing so is a cross-domain refactor
(loan engine, escrow, interest projection, debt strategy) that
HIGH-05's scope does not authorize.

### Recommended next step

After the remediation final gate, file a one-commit refactor that
imports ``MONTHS_PER_YEAR`` at each of the four sites and removes the
local ``TWELVE`` / ``MONTHS_IN_YEAR`` aliases. Targeted suites:
``test_loan_resolver*``, ``test_escrow_calculator``,
``test_interest_projection``, ``test_debt_strategy_service``.

---

## F-16. `app/utils/formatting.pct_to_decimal` is now an unused helper

- **Surfaced during:** Commit 24 (`fix(schema): reconcile Marshmallow
  domains with DB CHECK (E-28, HIGH-06)`).
- **Status:** resolved by Commit 3 of the follow-up plan
  (`chore(utils): delete unused pct_to_decimal helper (F-16)`).
  ``app/utils/formatting.py`` was deleted outright (the module
  contained only ``pct_to_decimal`` and its private ``_HUNDRED``
  constant); the lone test docstring reference in
  ``tests/test_routes/test_loan.py`` was rewritten to cite the
  schema-side ``_normalize_percent_fields`` helper.

### Problem

Commit 24 moved every "percent input -> stored fraction" conversion
from the route layer into the Marshmallow schema's ``@pre_load``
hook.  The schemas now divide by 100 themselves before the
``Range`` validator runs, so the production routes
(`accounts.update_interest_params`, `loan.create_loan_params`,
`loan.update_params`, `loan.add_rate_change`, `loan.add_escrow`,
`loan.refinance`, `settings.update`) no longer call
``app.utils.formatting.pct_to_decimal`` and the import was removed
from ``app/routes/loan.py``.

A repo-wide grep shows the function has no remaining production
callers and is referenced only by two pre-Commit-24 comments in
``app/models/loan_features.py`` (factually outdated -- they
describe the route-layer conversion that no longer happens; the
comments were rewritten in Commit 24 to cite the schema-layer
conversion) and by a one-line docstring reference in a single
``tests/test_routes/test_loan.py::test_params_update`` test
(describes the historical conversion site for context).

### Recommended next step

A small follow-up commit deletes ``app/utils/formatting.py`` (or
narrows it to whatever else lives there in the future), removes
the now-stale test docstring sentence, and confirms no
``pct_to_decimal`` import remains anywhere in the repo.  Out of
scope for HIGH-06 because the file deletion is unrelated to the
domain-reconciliation defect.

### File / line surfaced

- `app/utils/formatting.py:12` -- the function definition.

---

## F-17. Investment-params and pension-profile percent conversions still happen at the route layer, not schema @pre_load

- **Surfaced during:** Commit 24 (`fix(schema): reconcile Marshmallow
  domains with DB CHECK (E-28, HIGH-06)`).
- **Status:** resolved by Commit 12 of `remediation_follow_up_plan.md`
  (`refactor(schemas): unify percent conversion at @pre_load for
  investment + pension (F-17)`).  Investment + pension + retirement-
  settings schemas now own the percent-to-fraction conversion via
  ``@pre_load``; the route-layer ``_convert_percentage_inputs`` helper
  and the three inline ``Decimal("100")`` divisions in
  ``app/routes/retirement.py`` are gone.

### Problem

Commit 24 standardised the percent-to-fraction conversion at the
**schema** ``@pre_load`` boundary for every rate / threshold
schema HIGH-06 named (InterestParams, LoanParams, RateChange,
Refinance, EscrowComponent, UserSettings).  Two pre-existing
schemas reach the same end-state (schema validates the fraction,
DB CHECK accepts the same fraction) but do so by having the route
convert *before* invoking the schema:

  - ``app/routes/investment.py:_convert_percentage_inputs`` --
    rewrites the form payload in place before
    ``schema.load`` / ``schema.validate`` for
    ``InvestmentParamsCreateSchema`` /
    ``InvestmentParamsUpdateSchema``.
  - ``app/routes/retirement.py`` -- three sites at
    ``:118``, ``:214``, ``:351`` divide ``benefit_multiplier`` /
    SWR slider / generic-form-field by ``Decimal("100")`` before
    ``schema.load``.

Both routes work correctly today; the schemas validate fractions
in line with the DB CHECK.  The inconsistency is purely stylistic:
adding ``_PERCENT_FIELDS`` + the shared
``_normalize_percent_fields`` helper to those two schemas would
collapse all rate-conversion logic at the schema boundary, the
route would stop manipulating the form payload, and the
docstring in ``app/schemas/validation.py`` could drop its
"Two pre-existing schemas..." carve-out.

### Recommended next step

A small follow-up commit:

1. Adds ``_PERCENT_FIELDS`` + ``normalize_inputs`` to
   ``InvestmentParamsCreateSchema`` /
   ``InvestmentParamsUpdateSchema`` matching the Commit-24
   pattern.
2. Removes ``_convert_percentage_inputs`` from
   ``app/routes/investment.py``.
3. Same for the retirement-route conversion sites; the
   ``PensionProfileCreateSchema`` /
   ``PensionProfileUpdateSchema`` /
   ``RetirementSettingsSchema`` gain the same ``@pre_load``
   helper.
4. Drops the carve-out paragraph in
   ``app/schemas/validation.py`` so the convention is universal.

Targeted suites: ``test_schemas/test_c24_domain_reconciliation.py``
(unchanged), ``test_routes/test_investment.py``,
``test_routes/test_retirement.py``.

### File / line surfaced

- `app/routes/investment.py:766-784` -- ``_convert_percentage_inputs``.
- `app/routes/retirement.py:118`, `:214`, `:314`, `:318`, `:351`.

---

## F-18. `loan_params.interest_rate` has no upper-bound CHECK

- **Surfaced during:** Commit 24 (`fix(schema): reconcile Marshmallow
  domains with DB CHECK (E-28, HIGH-06)`).
- **Status:** resolved by Commit 13 of `remediation_follow_up_plan.md`
  (`fix(schema): add upper-bound CHECK on loan_params.interest_rate
  (F-18)`).  Destructive migration ``c66a9a7fda5a`` adds
  ``ck_loan_params_interest_rate_upper`` (``interest_rate IS NULL OR
  interest_rate <= 1``) with an upgrade-time pre-check that raises if
  any existing row would violate the new bound; the matching
  ``__table_args__`` constraint lives on ``LoanParams``.  Migration
  round-trip (upgrade -> downgrade -> upgrade) verified clean at gate
  time.

### Problem

Re-grep during Commit 24 verification surfaced an asymmetry in the
storage-tier CHECKs across the three "rate" tables:

```
app/models/interest_params.py:33-36   apy            >= 0 AND <= 1
app/models/loan_features.py:44-47     rate_history   >= 0 AND <= 1
app/models/loan_params.py:63-66       loan_params    >= 0          (no upper)
app/models/investment_params.py:21-24 assumed_return >= -1 AND <= 1
app/models/loan_features.py:111-114   escrow infl.   IS NULL OR
                                                     (>= 0 AND <= 1)
```

``loan_params.interest_rate`` accepts any non-negative storage
value; a raw-SQL writer or a future regression could commit a
post-conversion fraction of e.g. ``Decimal("9.5")`` (a 950% APR)
without storage-tier rejection.  The Marshmallow schema's new
``Range(0, 1)`` (Commit 24) and the route-tier validation pin the
upper bound at the application tier, but the DB tier is the
load-bearing belt-and-suspenders for raw-SQL writers.

### Recommended next step

A destructive migration adds ``ck_loan_params_interest_rate_upper``
= ``interest_rate IS NULL OR interest_rate <= 1`` (the NULL
admission preserves the E-18 / Commit 15 demotion).  Requires
explicit developer approval per the coding-standards rule for
constraint additions; the migration carries a ``Review:`` line
and a working downgrade (which is trivial -- drop the new CHECK).
Targeted suites: ``test_routes/test_loan.py``,
``test_schemas/test_c24_*``.

### File / line surfaced

- `app/models/loan_params.py:63-66` -- the asymmetric CHECK.

---

## F-19. `calculate_investment_inputs` Step 2 treats lump-sum transfers as periodic

- **Surfaced during:** Commit 25 (`fix(investment): unify employer-match
  across card/chart/year-end (HIGH-07)`).
- **Status:** resolved by Commit 16 of `remediation_follow_up_plan.md`
  (`fix(year-end): investment projection via contribution timeline
  (F-19)`).  Locked direction Option 1:
  ``_project_investment_for_year`` now calls
  ``build_contribution_timeline`` over the year's pay periods and
  passes the per-period map to ``growth_engine.project_balance``,
  mirroring the dashboard route's already-correct shape.
  ``calculate_investment_inputs`` itself is unchanged.

### Problem

`app/services/investment_projection.py:142-161`
(`calculate_investment_inputs` Step 2 -- "Transfer-based contributions
(average per period)") computes:

```python
total_contrib                = sum(t.estimated_amount for t in active_contributions)
num_periods_with_contrib     = len(set(t.pay_period_id for t in ...))
periodic_contribution       += total_contrib / num_periods_with_contrib
```

For a user with ONE large one-time settled transfer (e.g. an end-of-year
lump-sum contribution of $23,300 to a 401(k)) plus a recurring $1,500
deduction, this returns:

```
periodic_contribution = 1500 + 23300 / 1 = 24800   # wrong as a "per period" amount
```

The lump sum is treated as a typical periodic contribution.  The
investment dashboard route partially compensates by passing a
`contributions` timeline built from `build_contribution_timeline` to
`growth_engine.project_balance` -- the engine then uses the real
per-period amount from the timeline, not the inflated
`periodic_contribution` fallback.

The year-end `_project_investment_for_year`
(`app/services/year_end_summary_service.py:1031-1174`) does NOT pass
`contributions`; it passes `periodic_contribution` as the per-period
amount for every year period.  For the lump-sum-plus-recurring fixture
that means every period in the year is projected with a $24,800
employee contribution (immediately capped by the annual limit), which
overstates the employer match in early periods and badly distorts
`investment_growth` for the year.

This is distinct from HIGH-07 (which was specifically the dashboard
card bypassing the cap).  HIGH-07's three-surface equality holds for
the deduction-only and recurring-transfer cases (the cases the audit
focused on); the lump-sum case is a Step 2 modelling bug that predates
HIGH-07.

### Recommended next step

Two options to discuss with the developer (this is the kind of design
choice CLAUDE.md rule 8 calls out):

1. **Year-end uses the contribution timeline.**  Refactor
   `_project_investment_for_year` to call `build_contribution_timeline`
   over the year's pay periods and pass it to `project_balance` (same
   shape the dashboard route already uses).  No change to
   `calculate_investment_inputs`.  Lowest scope; preserves the
   existing dashboard semantics.
2. **`calculate_investment_inputs` returns a typed-by-source breakdown.**
   Separate the deduction-driven recurring component from the
   transfer-based component, expose both, and let consumers pick.
   Higher scope; more honest about what the value represents but
   touches every caller.

Option 1 is the lower-risk fix and aligns year-end with the dashboard
chart.  Either way, add a fixture (one lump-sum settled transfer +
one recurring deduction) and lock the year-end employer/growth totals
to hand-computed values that respect the per-period contribution
history.

### File / line surfaced

- `app/services/investment_projection.py:142-161` -- Step 2 averaging.
- `app/services/year_end_summary_service.py:1107-1163` --
  `_project_investment_for_year` consumes the averaged value as a
  per-period amount.

---

## F-20. Investment / retirement / year-end / route consumers still read off-engine ``salary_gross_biweekly``

- **Surfaced during:** Commit 26 (`fix(savings): DTI gross from
  raise-aware paycheck producer (MED-06)`).
- **Status:** resolved by Commit 17 of `remediation_follow_up_plan.md`
  (`refactor(income): canonical raise-aware gross-biweekly helper
  (F-20)`).  Locked direction Option 1: every consumer reads the
  raise-aware per-period gross from one canonical helper that wraps
  the paycheck engine; the off-engine
  ``Decimal(str(profile.annual_salary)) / pay_periods`` pattern is
  removed from the listed call sites.

### Problem

Commit 26 routes the savings-dashboard DTI denominator through the
canonical raise-aware paycheck engine (``calculate_paycheck`` ->
``PaycheckBreakdown.gross_biweekly``).  Five sibling call sites read
the same off-engine ``annual_salary / pay_periods`` quantity
(``_apply_raises`` not invoked, bare ``.quantize(Decimal("0.01"))`` =
banker's-default rounding -- the A-01 / F-032 drift pattern) and
remain unmigrated:

```
app/services/savings_dashboard_service.py:290-297  -- _load_account_params
                                                     (the producer)
app/services/savings_dashboard_service.py:544      -- investment-projection
                                                     consumer
app/routes/investment.py:110-117, :169             -- /investment dashboard
app/routes/investment.py:467-474, :523             -- /investment growth-chart
app/services/retirement_dashboard_service.py:429-432, :505
app/services/year_end_summary_service.py:1970+ (``_load_salary_gross_biweekly``)
  consumed at :186, :1065, :1114, :1608, :1644
```

Each of these is the same shape: ``Decimal(str(profile.annual_salary))
/ (profile.pay_periods_per_year or 26)`` quantized at 2dp with no
explicit ``rounding=`` mode.  For any user with an applicable
``SalaryRaise`` the value diverges from the paycheck engine's per-
period gross, and the employer-match / investment-projection /
retirement-gap / year-end-employer figures that consume it drift by
the same factor as the audit's F-032 worked example
($107,120 vs $104,000 = ~2.99% understatement).

The MED-06 finding scope is specifically "DTI gross denominator on
``/savings``"; the audit notes the duplication at
``02_concepts.md:1419-1436`` (Pair D = pension calculator, "cross-link
only -- it feeds ``pension_benefit_*``, owned by P3-d2; NOT
re-verdicted here") but treats the investment / retirement / year-end
consumers as adjacent territory.  Commit 26's brief explicitly says
"Stay in scope" -- these sites are flagged here, not folded in.

### Recommended direction

Two options, in order of cost:

1. **Lift the engine call to one service** (``income_service`` or
   similar).  Every consumer that wants the raise-aware per-period
   gross calls one canonical function that wraps
   ``calculate_paycheck``.  ``_load_account_params``'s
   ``salary_gross_biweekly`` field can then either be removed
   (callers fetch via the service directly) or backfilled from the
   service so the off-engine recompute disappears entirely.
2. **Local migration per consumer.**  Each consumer adopts the
   ``_get_current_paycheck_breakdown`` pattern from Commit 26
   in-place.  Lower abstraction cost, higher repetition.

Option 1 is preferred because the consumer count is large enough that
the wrapping helper amortises (six current call sites; one centralised
boundary).  It also fits the "one income producer" principle that
MED-06 establishes for the DTI path.

### Why defer

Commit 26's charter is the DTI denominator (the F-032 finding's
governed pair).  The investment / retirement / year-end consumers
each have their own employer-match / contribution-limit / gap-funding
math that interacts with raises differently from a DTI ratio; folding
all five into one commit would multiply the test surface and obscure
the F-032 fix.  Each consumer is also distinct enough that a single
migration cannot blindly substitute the engine value (the year-end
``ctx`` shape and the retirement weighted-return path have their own
contracts), so the work is better done as a focused refactor after
the remediation final gate.

### Acceptance criteria for the eventual PR

- Every off-engine ``salary_gross_biweekly`` site reads from a single
  shared engine helper (option 1) or a per-consumer engine call
  (option 2).  No bare
  ``Decimal(str(profile.annual_salary)) / profile.pay_periods_per_year``
  pattern remains in ``app/services/`` or ``app/routes/`` outside
  ``_load_account_params`` itself (which may keep the symbol if the
  helper is named-renamed and the value comes from the engine).
- Targeted suites: ``test_investment.py``, ``test_retirement.py``,
  ``test_year_end_summary_service.py``,
  ``test_retirement_dashboard_service.py`` -- each gains a raise-
  applicable fixture asserting the engine-derived value drives the
  consumer's downstream math.
- Full pytest suite passes; pylint clean.

---

## F-21. Unify savings-dashboard loan projection through a single balance-map dispatcher

- **Surfaced during:** Commit 28 (`refactor(investment): extract
  dashboard service; collapse dispatcher; DTO (MED-01)`).
- **Status:** resolved by Commit 19 of `remediation_follow_up_plan.md`
  (`refactor(loan): unify period-balance dispatcher; period-end-keyed
  canonical (F-21)`).  Locked canonical: period-end-keyed (year-end
  semantic).  ``account_projection.compute_loan_period_balance_map``
  is the single producer; both
  ``savings_dashboard_service._compute_account_projections`` and
  ``year_end_summary_service._get_account_balance_map`` consume it.
  Savings-dashboard 3/6/12-month projected balances re-pinned with
  hand-computed period-end arithmetic.

### Problem

S6-03 in `06_dry_solid.md` flags the dual per-account-type dispatcher
(`savings_dashboard_service._compute_account_projections` vs
`year_end_summary_service._get_account_balance_map`).  Commit 28
collapsed the *classification* step into one
`account_projection.classify_account` shared by both consumers, but
did NOT fully unify the loan-path balance derivation: the savings
dashboard still walks `state.schedule` to find rows on-or-before
`date(target_y, target_m, 1)` for the 3 / 6 / 12-month projected
balances (`savings_dashboard_service.py:445-453`), while the year-end
summary's debt path reads from `debt_schedules` via
`_schedule_to_period_balance_map` keyed on `period.end_date`.

These two derivations answer slightly different questions and produce
different cents-precise values for the same loan + same horizon, so
unifying them through one balance-map dispatcher would change one of
the two displayed numbers.  Commit 28's verification gate is "outputs
byte-identical to before this commit", so the unification was
deferred.

### Resolution sketch

Decide (with the developer) whether the savings-dashboard loan
projection should adopt the year-end period-end-keyed semantic, or
whether the year-end derivation should adopt the target-month-first
semantic.  Either direction is a behavioral change (the displayed
numbers move).  Once the canonical semantic is chosen, route both
consumers through one `compute_loan_period_balance_map` shared in
`account_projection.py`.

### Acceptance criteria for the eventual PR

- One `compute_loan_period_balance_map` (or equivalent) producing the
  `period_id -> remaining_balance` map for an amortising account,
  consumed by both the savings-dashboard loan card and the year-end
  net-worth liability + debt-progress sections.
- `savings_dashboard_service._compute_account_projections` loan
  branch reads from the shared dispatcher; the
  `for label, month_offset in [...]` reverse walk over
  `state.schedule` is gone.
- Targeted suites
  (`test_services/test_savings_dashboard_service.py`,
  `test_services/test_year_end_summary_service.py`) are updated to
  pin the chosen canonical balance for at least one fixture loan at
  one target horizon, with the hand-computed arithmetic in a comment.

---

## F-22. Extract shared deduction-loader and projection-inputs helpers across investment surfaces

- **Surfaced during:** Commit 28 (`refactor(investment): extract
  dashboard service; collapse dispatcher; DTO (MED-01)`).
- **Status:** resolved by Commit 18 of `remediation_follow_up_plan.md`
  (`refactor(investment): extract shared deduction-loader +
  projection-inputs helpers (F-22)`).  Both duplicates are gone:
  ``investment_projection.load_active_deductions_for_account`` and
  ``build_investment_projection_inputs`` are consumed by the three
  dashboard services.  Gate-time
  ``pylint --disable=all --enable=R0801`` over the trio scores
  10.00/10 (no remaining duplicate-code matches for F-22's targets).

### Problem

After extracting `investment_dashboard_service.py` (S6-01), pylint
flags two R0801 (similar-lines) duplicates:

1. The active-paycheck-deduction filter query duplicates
   `investment_dashboard_service._load_deductions_for_account` against
   `retirement_dashboard_service.py:399-404` (same filter shape:
   `SalaryProfile.user_id == user_id`, `is_active.is_(True)`, ...).
2. The `calculate_investment_inputs(...)` kwargs splat duplicates
   between `investment_dashboard_service.py:254-263` and
   `savings_dashboard_service.py:554-559` (same six keyword
   arguments).

Both are inherent to the engine API shape rather than a logic
duplication, but consolidating either would shave the duplicate-code
warning and centralise the engine-input contract.

### Resolution sketch

Two options:

- **Option A.** Move the active-deduction filter into a small helper
  in `app/services/account_projection.py` (or a sibling module),
  consumed by `investment_dashboard_service` and
  `retirement_dashboard_service` -- closes the (1) duplicate.
- **Option B.** Introduce a shared `build_investment_projection_inputs`
  helper in `app/services/investment_projection.py` (the natural
  home, since it already owns `calculate_investment_inputs`) that
  takes (account_id, params, user_id, all_periods, current_period)
  and returns the engine-ready `InvestmentInputs` plus the adapted
  deductions / shadow-income lists.  This closes both (1) and (2) at
  the cost of one larger touch.

### Acceptance criteria for the eventual PR

- The R0801 duplicate-code findings comparing
  `investment_dashboard_service` to
  `retirement_dashboard_service` / `savings_dashboard_service` are
  gone (pylint output verifies).
- Each surface still has its own thin orchestrator that calls the
  shared helper -- the helper does not absorb dashboard-specific
  display logic (which would re-create a fan-in monolith).
- Outputs unchanged across `test_routes/test_investment.py`,
  `test_routes/test_retirement.py`,
  `test_services/test_savings_dashboard_service.py`,
  `test_services/test_year_end_summary_service.py`.

---

## F-23. Companion route `_build_entry_data` casts a Decimal pct through float

- **Surfaced during:** Commit 30 (`fix(dashboard): entry-tracked bill row
  single disclosed base (E-21, MED-03)`).
- **Status:** resolved by Commit 9 of `remediation_follow_up_plan.md`
  (`fix(companion): move entry pct derivation to service helper
  (F-23)`).  ``entry_service.pct_complete`` is the canonical helper
  (Decimal in, Decimal out, clamped to ``[0, 100]``); the route's
  ``float(Decimal_expression)`` cast at ``app/routes/companion.py``
  is gone.

### Problem

`app/routes/companion.py:54-55` computes
``pct = float(total / txn.estimated_amount * Decimal("100"))`` -- a
Decimal-only arithmetic expression then cast through binary float to
satisfy a progress-bar width consumer.  The cast lives in a route
(violating the project standard that money math is service-layer
Decimal), and the float result is numerically consistent today only
because the call site is display-only.  The audit cross-references the
inline pct from F-028's row (`03_consistency.md:2223` R2) as a
float-on-money / route-arithmetic concern (E-10 / E-16); the
cross-anchor row inconsistency itself is closed by Commit 30, but the
float cast is the MED-04 / E-16 surface.

### Resolution sketch

Move the pct derivation into a small helper in `entry_service` (or the
companion service if one is extracted) that returns a Decimal capped at
100 (analogous to `_safe_pct_complete` in `dashboard_service.py`), then
have the template/Jinja consume the Decimal directly.  Eliminate the
`float(...)` cast at the call site.

### Acceptance criteria for the eventual PR

- `grep -n "float(" app/routes/companion.py` shows no money-math casts.
- The pct value rendered for the companion progress bar is byte-
  identical to the current display for every fixture in
  `tests/test_routes/test_companion_routes.py`.
- The helper has a substantive docstring naming MED-04 / E-16 as the
  governing standard.

---

## F-24. Duplicated recurrence-rule construction and stale-conflict handlers between `templates.py` and `transfers.py`

- **Surfaced during:** Commit 8 of the follow-up plan (F-14 defense-
  in-depth filter on `hard_delete_transfer_template`).
- **Status:** resolved by Commit 2 of
  `remediation_follow_up_F24_F25_F26_plan.md`
  (`refactor(routes): extract recurrence-rule and stale-conflict
  helpers for templates/transfers (F-24)`).  Two new helpers in
  `app/routes/_recurrence_form_helpers.py`
  (`build_recurrence_rule_from_form`, `handle_stale_conflict`)
  close all 9 pre-extraction R0801 pairs the entry named (plus 6
  wider stale-conflict variations folded in per the plan's
  R-FU-3 scope addition).  The existing-rule mutation branch in
  `update_template` / `update_transfer_template` stays inline per
  the plan's Section 2 design decision (extracting it would
  replace nine `setattr`-style assignments with a helper whose
  body is identical to the inline form).

### Problem

`pylint app/routes/transfers.py app/routes/templates.py
--enable=duplicate-code` reports three duplicated blocks between the
transaction-template and transfer-template CRUD routes:

1. **Recurrence-rule construction in the *create* paths**
   (`templates.py:191-201` vs `transfers.py:169-179`) -- the
   `start_period_id` / `end_date` pop, pattern lookup, `interval_n` /
   `offset_periods` derivation, and `RecurrenceRule(...)` instantiation
   are identical except for the absence of `due_day_of_month` on the
   transfer side.
2. **Recurrence-rule construction in the *update* paths**
   (`templates.py:353-362` vs `transfers.py:362-371`) -- the
   `else: rule = RecurrenceRule(...)` block when the existing template
   has no rule.
3. **`RecurrenceConflict` + `StaleDataError` flash handlers**
   (`templates.py:429-442` vs `transfers.py:434-447`) -- identical
   conflict-message formatting plus the rollback + flash + redirect
   shape on stale data.

Each pair has one route-specific axis -- the `due_day_of_month` field
on `RecurrenceRule` is populated only from `TemplateCreate/Update`
schemas, and the redirect URLs differ -- so a literal extraction is
not a one-liner.

### Why deferred

The duplication is stylistic, not behavioural: every pair processes
validated Marshmallow output identically, and the rule shape is
enforced by `RecurrenceRule` itself.  Extracting now would touch all
four CRUD entry points (`create_template`, `update_template`,
`create_transfer_template`, `update_transfer_template`) and require a
sweep across the template + transfer-template route test suites for
no behavioural delta.  Commit 8's F-14 scope is the
`hard_delete_transfer_template` bulk-delete filter; adding the
extraction here would exceed the plan's stated commit boundary.

### Suggested implementation

Two-helper extraction in a new `app/routes/_recurrence_form_helpers.py`
(or fold into `app/services/recurrence_engine.py` if a service-layer
home fits the existing module shape):

- `build_recurrence_rule_from_form(data, user_id) -> RecurrenceRule |
  None` -- consumes the validated Marshmallow payload, pops the
  recurrence fields, returns a fresh `RecurrenceRule` or `None` when
  no pattern was selected.  The `due_day_of_month` axis is handled by
  the helper reading the key only when present in `data` (Marshmallow
  `EXCLUDE` means the transfer payload simply omits it).
- `handle_stale_conflict(*, logger, route_id, edit_endpoint, **redirect_kwargs)`
  -- the shared StaleDataError + IntegrityError rollback + flash +
  redirect pattern.  Takes a `redirect_endpoint` keyword so each route
  controls its own destination.

The `RecurrenceConflict` flash message is identical between the two
routes and can become a module-level format string consumed by both.

### Acceptance criteria

- `pylint app/routes/transfers.py app/routes/templates.py
  --enable=duplicate-code` returns no `R0801` messages for the three
  pairs above.
- All existing template + transfer-template CRUD tests pass unchanged
  (the extraction is byte-equivalent at the wire level).
- New unit tests for the helpers in
  `tests/test_routes/test_recurrence_form_helpers.py` or equivalent,
  pinning the no-pattern, every-N-periods, and stale-conflict
  redirect branches.

---

## F-25. Pylint R0401 cyclic-import warnings in the split `app/routes/accounts` package

- **Surfaced during:** Commit 22 of the follow-up plan
  (`chore(release): follow-up final gate`), gate-time pylint run after
  Commit 21 split the historical monolithic `app/routes/accounts.py`
  into a per-sub-domain package.
- **Status:** resolved by Commit 1 of
  `remediation_follow_up_F24_F25_F26_plan.md`
  (`refactor(routes): move accounts_bp to dedicated module to break
  import cycle (F-25)`).  The blueprint declaration moved to a leaf
  module `app/routes/accounts/_bp.py`; each per-sub-domain
  submodule imports the blueprint from there directly, breaking
  the package <-> submodule round-trip pylint flagged.  The
  package init re-exports the symbol so consumers that do
  `from app.routes.accounts import accounts_bp` (notably
  `app/__init__.py`'s factory-time registration) resolve without
  an edit.  The re-export contract is pinned by
  `tests/test_routes/test_accounts.py::TestAccountsBlueprintReExport::test_package_reexports_same_blueprint_instance`.

### Problem

`pylint app/ --fail-on=E,F` reports four `R0401 Cyclic import`
warnings rooted at `app/utils/account_validation.py:1`:

```
app/utils/account_validation.py:1:0: R0401: Cyclic import
  (app.routes.accounts -> app.routes.accounts.detail)
app/utils/account_validation.py:1:0: R0401: Cyclic import
  (app.routes.accounts -> app.routes.accounts.crud)
app/utils/account_validation.py:1:0: R0401: Cyclic import
  (app.routes.accounts -> app.routes.accounts.anchor)
app/utils/account_validation.py:1:0: R0401: Cyclic import
  (app.routes.accounts -> app.routes.accounts.types)
```

These flow from the Flask blueprint split landed in Commit 21
(F-1).  The package's `__init__.py` instantiates `accounts_bp` and
imports each sub-module for the side-effect of registering its
route decorators; each sub-module imports `accounts_bp` (and
shared helpers from `app.utils.account_validation`) from the
package, which pylint traces back to the package init and reports
as cyclic.

The cycle is real but benign at runtime: the sub-module imports
resolve cleanly because each one only reads `accounts_bp` after the
package init has already created it.  Score impact is zero
(`9.57/10 (previous run: 9.57/10, +0.00)` at gate time); no E/F
messages; only refactor warnings.

### Recommended direction

Two options, in order of cost:

- **(a) Accept the warning** (status quo).  Standard Flask blueprint
  splits with shared validation helpers tend to produce this shape;
  refactoring it away costs more than the warning is worth.
- **(b) Move `accounts_bp` declaration to a dedicated module**
  (e.g. `app/routes/accounts/_bp.py`) and have both `__init__.py`
  and every sub-module import the blueprint from there.  Eliminates
  the package -> submodule -> package round-trip pylint flags as
  cyclic.  Mechanical refactor across the five files.

### Why defer

Behaviourally a no-op; no E/F messages; the score baseline is
unaffected.  The split itself (Commit 21) is otherwise clean.
Accepting the warning is the pragmatic choice unless a future
refactor in this package would benefit from breaking the cycle.

### Acceptance criteria for the eventual PR

- `pylint app/ --fail-on=E,F` reports zero `R0401` matches rooted in
  `app/routes/accounts/` or `app/utils/account_validation.py`.
- Every sub-module still registers its decorators against the
  shared `accounts_bp` blueprint.
- The full pytest suite passes with no test edits.

---

## F-26. Residual R0801 duplicates between `templates.py` and `transfers.py` beyond F-24's scope

- **Surfaced during:** Commit 2 of `remediation_follow_up_F24_F25_F26_plan.md`
  (`refactor(routes): extract recurrence-rule and stale-conflict
  helpers for templates/transfers (F-24)`), gate-time
  `pylint --disable=all --enable=R0801` run.
- **Status:** partially resolved by Commit 3 of
  `remediation_follow_up_F24_F25_F26_plan.md`
  (`refactor(routes): extract stale-form and recurrence-conflict
  helpers for templates/transfers (F-26 partial)`).  Pairs 1
  (stale-form check, optimistic-locking version_id mismatch) and
  2 (RecurrenceConflict logger.warning + flash) are closed via
  two new helpers in `app/routes/_recurrence_form_helpers.py`
  (`handle_stale_form_conflict`, `handle_recurrence_conflict`).
  Pairs 3, 4, 5 (list-rendering shapes) and pair 6 (hard-delete
  archive-fallback body wrapping) are explicitly deferred per
  the plan's Section 9 with acceptance criteria for the eventual
  cleanup PR.  Each deferred pair needs its own design
  discussion (different abstraction for list rendering;
  model-specific soft-delete callable for the archive-fallback
  body) that did not fit the F-26 commit's scope.

### Problem

F-24's two helpers
(`build_recurrence_rule_from_form`, `handle_stale_conflict`) closed
the 9 R0801 pairs the F-24 entry named, but
`pylint --disable=all --enable=R0801 app/routes/templates.py
app/routes/transfers.py` still reports six additional pairs that
were never in F-24's scope.  Listed here so a future cleanup has a
single entry to plan against rather than rediscovering them via
pylint output:

1. **Stale-form check (optimistic-locking version_id mismatch)**
   in the update routes.
   `templates.py:[319:339]` vs `transfers.py:[338:355]` -- the
   `if submitted_version is not None and submitted_version !=
   template.version_id: ... logger.info(... flash(... redirect(...)`
   block is byte-identical apart from the route id and the
   redirect endpoint.  Sibling to the F-24
   `handle_stale_conflict` extraction; a
   `handle_stale_form_conflict` helper would close this pair the
   same way.
2. **RecurrenceConflict logger.warning + flash** in the update
   routes.  `templates.py:[418:431]` vs `transfers.py:[430:443]`.
   The auto-keep-overrides Phase-1 flash carries identical copy
   apart from the noun ("transaction" vs "transfer"); a
   `handle_recurrence_conflict(noun, conflict)` helper would
   close it.
3. **List-template rendering blocks.**
   `templates.py:[133:147]` vs `transfers.py:[105:112]` -- the
   "load active + archived templates, partition into two lists,
   render the list page" pattern repeats with one route-specific
   axis (model class).  Less obvious cleanup; the partition
   logic could become a small helper but the render template
   path differs.
4. **List-template rendering variant.**
   `templates.py:[251:257]` vs `transfers.py:[106:112]` -- same
   shape as (3) but for the variant where the form-template GET
   shares the lookup body.
5. **List-template rendering variant 2.**
   `templates.py:[141:147]` vs `transfers.py:[753:759]` -- same
   shape again, exposed by the per-template instance routes that
   re-query the same lists for the edit view.
6. **Hard-delete archive-fallback bodies.**
   `templates.py:[577:587]` vs `transfers.py:[629:640]` -- F-24's
   `handle_stale_conflict` collapsed the inner `try/except` but
   the surrounding "`if template.is_active: template.is_active =
   False; <soft-delete projected rows via model-specific query>;
   try: db.session.commit() ...`" structure remains duplicated
   because the soft-delete query is model-specific
   (`Transaction` vs `Transfer` with shadow-aware service
   delegation).  Closing this would require lifting the
   soft-delete loop into a shared archive helper, which has
   non-trivial design implications.

### Why deferred

None of the six pairs are in the F-24 scope and each one is its
own design question (especially the list-rendering pairs, which
need a different abstraction than the recurrence / stale-conflict
extractions F-24 used).  Pylint score at the F-24 gate landed at
9.58/10 (+0.01 vs the post-F-25 baseline 9.57/10), so the
residual duplicates do not regress the score.

### Recommended direction

Two helpers cover (1) and (2) cheaply:

```python
def handle_stale_form_conflict(*, logger, log_label, log_id,
                                submitted, current, flash_message,
                                redirect_endpoint,
                                redirect_endpoint_kwargs=None):
    """Optimistic-locking form-side conflict.

    Mirror of :func:`handle_stale_conflict` but for the pre-flush
    version_id mismatch path.  Logs both the submitted and current
    counters so post-mortem analysis can reconstruct the race.
    """

def handle_recurrence_conflict(*, logger, noun, conflict):
    """RecurrenceConflict auto-keep-overrides Phase-1 handler.

    Logs the override / delete counts and flashes the canonical
    "kept as-is" message with the route-specific noun.
    """
```

(3)-(5) need a list-rendering helper or a class-based view; not
worth the complexity for templates / transfers alone.

(6) needs a shared archive helper that takes a model-specific
soft-delete callable; design discussion warranted.

### Acceptance criteria for the eventual PR

- `pylint --disable=all --enable=R0801 app/routes/templates.py
  app/routes/transfers.py` reports zero matches for the six pairs
  named above.
- Existing templates / transfers CRUD route tests pass unchanged.
- New helper unit tests pin the stale-form-conflict and
  recurrence-conflict shapes (mirror of the F-24
  `tests/test_routes/test_recurrence_form_helpers.py` patterns).

---

## F-27. Honour projected recurring transfers in `calculate_payoff_by_date`'s "required extra"

- **Surfaced during:** Commit 7 of
  `docs/plans/2026-05-21-amortization-engine-split-implementation.md`
  (`refactor(loan): calculate_payoff_by_date and refinance on new
  primitives`). Deferred deliberately per design decision D-F of
  that plan (and Q-1 of the architectural plan
  `docs/plans/2026-05-21-amortization-engine-split-replay-projection.md`);
  Commit 7 is a behavior-preserving refactor only.
- **Status:** **RESOLVED (2026-06-11)** -- developer selected "fix + reframe,
  show both" from the Reconsideration note's option space (deep-hunt Batch AJ;
  register: `docs/audits/pylint-cleanup/deep-quality-hunt.md`).  The engine
  core was factored out as `required_extra_for_projection` (override-aware
  baseline + search; `calculate_payoff_by_date` delegates, C7 locks green),
  a new `loan_resolver.target_date_outlook` answers from the SAME
  replay/override prep the committed scenario uses (plan payoff date +
  additional extra on top of the plan), and the target-date partial leads
  with the plan-aware answer while keeping the raw no-plan figure as a
  secondary line.  In-window convention (overrides span the recurrence
  horizon; contractual beyond) -- the same convention the extra-payment
  tab's committed scenario already ships.  Nuance surfaced during testing:
  when a plan's window contribution is SMALLER than what the raw extra
  would have added over those months, the per-month top-up is legitimately
  HIGHER than the raw figure (override months suppress the searched extra,
  squeezing it into fewer months); both numbers render, so the display
  stays honest either way.  The history below is retained for the record.

  *(Superseded original status:)* not addressed; **PAUSED for UX
  reconsideration (2026-06-09).**
  The refactor onto `project_forward` was intentionally a pure structural
  migration; introducing `monthly_override` for projected payments here is a
  separate user-facing behavior change.  A 2026-06-09 implementation attempt
  was paused after the discovery in the Reconsideration note below.

### Problem

`amortization_engine.calculate_payoff_by_date` accepts a target
date and returns the Decimal extra-monthly payment required to
pay off the loan by that date.  It does NOT consume the user's
projected payments (the recurring transfer template that pays
the loan).  A user already paying $500/mo over contractual via
their template is told they need $X extra; in reality they need
$X - $500.  The route at
`app/routes/loan.py:payoff_calculate` `mode == "target_date"`
already has access to the projected payments via
`_load_loan_context` (`ctx["payments"]`) -- the function just
discards them.

The architectural plan refinement #2 spells out the latent issue
and the Phase 7 paragraph documents that fixing it would change
the displayed "required extra" for any user with a recurring
template paying over contractual.  Commit 7 of the
implementation plan honours that scope boundary explicitly and
flags this entry as the canonical follow-up.

### Recommended fix

Thread the projected portion of `payments` through
`calculate_payoff_by_date` as a `monthly_override`-shaped
parameter, then pass it to both the standard and binary-search
`project_forward` calls.  Pseudocode:

```python
def calculate_payoff_by_date(
    current_principal,
    annual_rate,
    remaining_months,
    target_date,
    origination_date,
    payment_day,
    original_principal=None,
    term_months=None,
    rate_changes=None,
    monthly_override=None,  # NEW
):
    # ... derive contractual and starting_date as today ...
    standard = project_forward(
        ...,
        monthly_override=monthly_override,
        extra_monthly=Decimal("0.00"),
    )
    # ... binary search with the same monthly_override ...
```

**Note (2026-06-05):** the pseudocode above shows the pre-param-object
positional signature.  As of the Phase 3 pylint-cleanup refactor
(`PayoffRequest` parameter object; see F-28), `calculate_payoff_by_date`
takes a single `PayoffRequest`, so the eventual fix adds the
projected-payments override as a field on `PayoffRequest` (or a sibling
argument), not a new positional parameter.

Route caller in `payoff_calculate`'s `mode == "target_date"`
branch builds the override dict from `ctx["payments"]` filtered
to projected entries past `as_of` (mirroring the composer's
projection routing in
`loan_resolver.compute_payoff_scenarios`).

### Decision required before authoring

This is a behaviour change, not a refactor.  Verify with the
solo developer:

1. Confirm the new "required extra" semantics are desirable
   (lower numbers for users with recurring templates).
2. Decide whether the UI should explain the change (a tooltip
   on the "Required Extra Monthly Payment" label noting that
   projected transfers are already counted).
3. Decide whether to keep a "raw" extra calculation alongside
   the new one (for users who want to know what they would need
   if they did not have the template).

### Acceptance criteria

- New unit test in
  `tests/test_services/test_amortization_engine.py` proves that
  a $500 / month override reduces required extra by ~$500
  versus the no-override baseline (the hand-computed reduction
  for the architectural plan's symptom scenario).
- New route test in `tests/test_routes/test_loan.py` proves the
  `mode == "target_date"` branch surfaces the lower required
  extra when a projected recurring transfer template exists.
- Existing C7-3 / C7-4 / C7-8 assert-unchanged locks updated to
  reflect the new behaviour (re-pinned per CLAUDE.md Rule 5's
  documented exception with hand-computed arithmetic per
  assertion).
- F-022 invariant + Commit 3 C3-10 regression lock remain
  green.

### Out of scope

- Refinance calculator (one-off projection; no template
  semantics).
- Composer (already honours projections via
  `monthly_override`).
- ARM rate-change handling (no change from current behaviour).

### Reconsideration note (2026-06-09) -- why this is PAUSED, not built

A scoped implementation attempt established three things by reading the live
engine (HEAD `16f7c9e`), which together argue for reconsidering the UX before
coding:

1. **The prescribed `monthly_override` fix delivers only a PARTIAL correction.**
   In `project_forward` an override month *replaces contractual AND suppresses
   `extra_monthly`* (`amortization_engine.py:581-591, 656-660` -- a deliberate
   regression-prevention property).  Projected payments only extend the
   recurrence horizon (~2yr).  So threading projected payments as
   `monthly_override` corrects "required extra" only across that window; beyond
   it the projection reverts to contractual.  This is the ratified, exact,
   additive, escrow-correct, low-risk option ("A").  It does NOT fully match the
   finding's prose framing ("they need $X - $500" implies the over-payment
   continues for the whole term).

2. **A full-term correction CANNOT use `monthly_override`.**  Extrapolating the
   template to every remaining month makes every month an override month, which
   suppresses the searched `extra_monthly` everywhere -- the binary search then
   has no month to vary and cannot compute a required additional extra.  Full
   term ("C") instead requires *raising the projection's `contractual_payment`
   baseline* to the user's steady recurring monthly P&I-equivalent and solving
   extra on top (mathematically: raw-required-extra minus current over-payment).
   That needs (a) deriving the steady recurring payment -- an APPROXIMATION,
   since the transfer cash amount mixes drifting escrow and, for a biweekly
   transfer, varies month to month; and (b) changing the displayed numbers
   (current payment / additional extra / new total).

3. **The single "Required Extra Monthly Payment" number is the real weak point.**
   For a user who already has a payment plan it is ambiguous (extra vs what
   baseline?) and either partial (A) or approximate (C).  The clean, EXACT datum
   the system already computes is the *committed scenario's payoff date* from
   `loan_resolver.compute_payoff_scenarios` (the user's actual projected plan).
   A reframe -- "your current plan pays off on DATE_Y; to hit your target add
   $Z/mo" -- may be a better UX than patching the single number.  Decide the UX
   before implementing.  Until then, "A" remains the safe fallback if a quick,
   exact, in-window correction is wanted without a display change.

---

## F-28. C15-3 lock allow-lists `amortization_engine.py` for the `PayoffRequest` param object

- **Surfaced during:** Phase 3 of the pylint-cleanup plan
  (`docs/audits/pylint-cleanup/plan.md`) -- the
  `calculate_payoff_by_date` decomposition that bundles its ten
  arguments into a `PayoffRequest` parameter object (Shape A,
  developer-chosen 2026-06-05).
- **Status:** resolved in place; no future work required.  Recorded
  here per the C15-3 lock's own protocol
  (`tests/test_models/test_loan_params_demoted.py`
  `test_no_display_read_of_current_principal`): "if they are new engine
  internals, document them ... and extend the allow-list."

### What changed

The refactor converts the bare `current_principal` parameter into a
`PayoffRequest.current_principal` field, so the function body now reads
`request.current_principal` in four places.  The C15-3 display-read
sweep greps `app/` for the `.current_principal` attribute-access syntax
and flagged all four.

### Why this is not a demoted-column read

`app/services/amortization_engine.py` is a pure-function engine: its
module docstring states "No database access -- operates only on values
passed in," and it imports no model (`calendar`, `logging`,
`dataclasses`, `datetime`, `decimal`, `app.utils.money` only).  It
therefore *cannot* read `LoanParams.current_principal` (the demoted
column the lock protects).  `PayoffRequest.current_principal` is a
parameter-object field; at the one production call site
(`routes/loan.py` `payoff_calculate`, `mode == "target_date"`) it is
fed `state.current_balance` -- the resolver-derived SSOT balance,
exactly the value E-18 / Commit 15 wants callers to use.  The lock
firing here is a false positive on syntax, not a contract violation.

### Resolution

`services/amortization_engine.py:` added to the `_ALLOWED` tuple with a
why-comment.  The file's structural inability to touch `LoanParams`
means the entry does not weaken the lock's real protection.

### Possible future improvement (low value, not scheduled)

The C15-3 sweep is a coarse grep that cannot distinguish
`PayoffRequest.current_principal` from `LoanParams.current_principal`.
An AST-based check keyed on the receiver's inferred type would remove
the need for file-granular allow-listing, but the grep is intentionally
simple and the allow-list is its designed extension point; type-aware
analysis is not worth the complexity today.

---

<!-- Add new follow-up entries above this line, numbered F-2, F-3, ... -->
