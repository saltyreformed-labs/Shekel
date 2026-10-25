> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Mobile-First v3 -- Follow-up Work

Tracks issues identified during the mobile-first v3 implementation
(`docs/implementation_plan_mobile_v3.md`) that are deliberately out of
scope for the commit that surfaced them. Each entry is self-contained
and can be picked up after the originating commit lands.

Cross-references:

- Plan: `docs/implementation_plan_mobile_v3.md`
- Common rules and work-summary format:
  `docs/audits/financial_calculations/remediation_follow_up_common.md`
  (rule 6 routes out-of-scope items to this file)

---

## F-1. Scope the `#mobile-grid` swipe-to-navigate handler to the Plan tab

- **Surfaced during:** Commit 6
  (`feat(mobile-grid): _mobile_this_period.html partial with arrows`).
- **Status:** closed (commit `90a2f5b`,
  `fix(mobile-grid): scope period-nav swipe handler to #mobile-plan`).
  Pre-existing latent bug introduced at Commit 5 (the tab scaffold)
  and made user-visible at Commit 6 (the default-tab flip to
  "This Period").  Replaced the
  ``document.getElementById('mobile-grid')`` binding-site in
  ``app/static/js/mobile_grid.js`` with
  ``document.getElementById('mobile-plan')`` so a swipe on the
  "This Period" tab no longer mutates the Plan tab's
  ``currentIndex``; added
  ``TestMobileSwipeAction::test_period_nav_swipe_scoped_to_plan_pane``
  as a source-level lock so a future refactor cannot silently
  re-introduce the cross-tab leak.

### Problem

`app/static/js/mobile_grid.js:42-60` attaches the horizontal-swipe
listener to `#mobile-grid` (the outer container that wraps both tabs)
rather than to `#mobile-plan`. The handler calls `navigate(delta)`,
which mutates `currentIndex` and `display` on the Plan tab's
`.mobile-period-panel` elements.

With Commit 6's default-tab flip ("This Period" is now active by
default), a horizontal swipe on the This Period tab silently advances
the Plan tab's `currentIndex`. The user sees nothing change because:

1. The This Period tab has no `.mobile-period-panel` elements (the
   partial uses a plain wrapper div by design -- the absence is what
   keeps the existing Plan navigation's `querySelectorAll` from
   skipping indexes).
2. Bootstrap's tab JS hides the Plan tab-pane via `display: none` on
   the outer `.tab-pane` element, so the inner `display: ''` /
   `display: 'none'` writes the `navigate()` function makes on
   `panels[currentIndex]` have no visual effect.

The bug becomes user-visible when the user switches from This Period
to Plan: the Plan tab is now showing a different period than the one
they last saw, with no explicit action that would have moved it.

### Latent before Commit 6

Commit 5 introduced the tab scaffold with a placeholder in This Period
and the existing flow in Plan. The handler was attached to
`#mobile-grid` then too, but a swipe on the placeholder still
advanced Plan -- the placeholder had no other content to compete for
the user's attention, so the gesture was unlikely to be triggered.
Commit 6 turns the This Period tab into the default-active surface
with real period content and prev/next arrows; the swipe-on-card
intent the user might bring to a real period now surfaces the
cross-tab leak.

### Recommended fix (estimated effort: 5 minutes)

In `app/static/js/mobile_grid.js`, replace

```javascript
var grid = document.getElementById('mobile-grid');
if (grid) {
    // ... touchstart / touchend listeners
}
```

with

```javascript
var planPane = document.getElementById('mobile-plan');
if (planPane) {
    // ... same touchstart / touchend listeners on planPane
}
```

`mobile-plan` is the Plan tab-pane; attaching the listeners there
constrains the gesture to the tab the navigation actually drives.
The This Period tab keeps its URL-driven prev/next arrows (Commit 6)
and -- if the user wants a horizontal-swipe shortcut on This Period
later -- a new gesture handler can be added on `#mobile-this-period`
that posts to the same `?periods=1&offset=N±1#this-period` URL the
arrows already use. Out of scope for F-1.

### Test coverage

No new test needed -- the existing
`tests/test_routes/test_grid.py::TestMobileThisPeriodPartial` covers
the partial's structural invariants, and the gesture behavior is
JS-side (no Python test infrastructure for touch events; the project
explicitly excludes this per `docs/implementation_plan_mobile_v3.md`
Section 14, "Out of scope" item 6). Manual verification at 375x812
in Firefox responsive mode is sufficient.

### Why defer

Commit 6's stated scope is the new partial, the include wiring, and
the default-tab flip. Modifying the swipe-handler binding is a
behavior change to `mobile_grid.js` outside that scope. Commit 7
already touches `mobile_grid.js` extensively (per the plan, it adds
the inline action-bar tap-to-toggle handler and removes the existing
tap-to-edit handler at lines 65-75); the swipe-scope fix can land
in the same commit at near-zero marginal cost.

---

## F-2. Containerize the dev Flask server to mirror production

- **Surfaced during:** Commit 6 manual-verification wiring
  (`feat(mobile-grid): _mobile_this_period.html partial with arrows`),
  when the dev Flask binding had to be moved from `127.0.0.1:5000`
  to `172.32.0.1:5000` (the shekel-frontend bridge gateway) so the
  shared nginx container could reach it.
- **Status:** open. Pure dev-environment refactor with no impact
  on the application code path; pick up when the mobile work has
  settled and parity-fidelity is the higher value than iteration
  speed.

### Problem

The dev Flask server runs on the bare host via
`flask run --host 172.32.0.1` while the production Shekel app runs
inside a Docker container behind Gunicorn behind the shared nginx,
on the `shekel-frontend` bridge with the audit triggers, two-role
DB policy, and entrypoint health checks that prod ships with.  Three
classes of drift result:

1. **Process-model drift.** Dev uses Werkzeug's single-threaded
   reloader; prod uses Gunicorn with multiple workers, gthread, the
   `pre_request` / `post_request` hooks, and the entrypoint
   `EXPECTED_TRIGGER_COUNT` health gate.  Concurrency bugs that
   only surface under multi-worker scheduling (per-process caches,
   first-request initialisation races, SIGTERM handling) are
   invisible in dev.
2. **Authorisation drift.** Prod runs the app under the
   least-privilege `shekel_app` Postgres role (cannot drop, alter,
   or replace audit triggers per
   `docs/coding-standards.md` "Audit Triggers").  Dev runs as
   `shekel_user`, the superuser-equivalent that owns the schema.
   Code that accidentally relies on schema-modifying privileges
   works in dev and fails in prod.  The pylint guard catches most
   of this, but a runtime path that issues a DDL statement (e.g.
   a poorly-scoped `CREATE TEMPORARY` cousin) would only surface
   in prod.
3. **Network-trust-boundary drift.** Prod's nginx terminates TLS
   and forwards via the `shekel-frontend` bridge to
   `shekel-prod-app:8000`; the app receives CF-Connecting-IP from
   the cloudflared sidecar and trusts the bridge subnet to inject
   it (`set_real_ip_from 172.32.0.0/24` in nginx.conf).  Dev's
   nginx (via `conf.d/shekel-dev.conf`) proxies to
   `172.32.0.1:5000` which is the bridge gateway, not the bridge
   member -- so `X-Real-IP` and `X-Forwarded-For` arrive from a
   different network position than prod.  Code that branches on
   either header (rate-limit zones, audit user attribution, the
   `shekel_app.current_user_id` session-local) behaves slightly
   differently in dev.

### Recommended approach (estimated effort: half a day)

Add a `dev` profile to `deploy/docker-compose.prod.yml` (or a new
`deploy/docker-compose.dev.yml` keyed on `name: shekel-dev` at the
top) that:

- Builds the same image the prod compose uses, but bind-mounts the
  repo at `/home/shekel/app` so code changes hot-reload through
  Gunicorn's `--reload` flag.
- Joins the same `shekel-frontend` external bridge so the shared
  nginx reaches it via Docker DNS (`shekel-dev-app:8000`) the same
  way it reaches prod (`shekel-prod-app:8000`).
- Uses a sibling Postgres container on a dedicated dev DB so the
  prod DB is untouchable from the dev path.
- Carries the same `cap_drop: [ALL]` / `read_only: true` /
  `tmpfs:` / non-root `user:` hardening per CLAUDE.md "Compose
  conventions" so the dev container has the same attack surface
  as prod.
- Defaults to a different `EXPECTED_TRIGGER_COUNT` only if the
  dev DB intentionally differs; otherwise reuses the same value.

`conf.d/shekel-dev.conf` then proxies to `shekel-dev-app:8000`
instead of `172.32.0.1:5000`, mirroring `shekel.conf`'s shape
exactly.  The `--host 172.32.0.1` workaround on the bare-host
`flask run` is retired; the dev workflow becomes
`docker compose -f deploy/docker-compose.dev.yml up`.

### Test coverage

The full pytest suite already runs against a per-worker template
clone of the prod schema (per `docs/testing-standards.md`).  The
containerised dev environment does not change the test path; it
only changes the manual-on-device verification path.  A new
`tests/test_deploy/test_dev_compose.py` could assert the dev
compose file has the same hardening keys as prod
(`security_opt: [no-new-privileges:true]`, `cap_drop: [ALL]`,
`read_only: true`), matching the existing `test_deploy/` audit
pattern.

### Why defer

This is dev/prod parity work, not user-visible product work.  The
mobile-first v3 plan has nineteen commits left after Commit 6; doing
the containerisation now would block them on an unrelated refactor.
After the v3 plan closes (Commit 28), the containerisation makes a
clean self-contained PR with no in-flight grid coupling.

If a mobile commit later in the plan needs Gunicorn-style behaviour
to verify (e.g. cookie binding under workers, SIGTERM behaviour
during the service-worker activate event), promote this item early
rather than papering over the divergence in the dev script.

---

## F-3. Remove `style="min-height: 44px;"` from `_mobile_card_actions.html`

- **Surfaced during:** Commit 9
  (`feat(mobile-grid): swipe-left reveals Mark Paid button on cards`).
- **Status:** closed (commit `c5de9e4`,
  `style(mobile): btn-touch-44 utility + replace 3 inline styles in _mobile_card_actions`).
  Pre-existing from Commit 7
  (`feat(mobile-grid): _mobile_plan.html + inline card action bar`).
  Replaced the three inline ``style="min-height: 44px;"`` attributes
  with a stand-alone ``.btn-touch-44`` utility class inside the
  existing ``@media (max-width: 767.98px)`` block in
  ``app/static/css/app.css``; added
  ``TestMobileCardActionBar::test_no_inline_style_attr_in_mobile_card_actions``
  as a regression lock.

### Problem

`app/templates/grid/_mobile_card_actions.html:66`, `:75`, and `:85`
each carry an inline `style="min-height: 44px;"` attribute on the
Mark Paid / Edit Amount / Open Full buttons. The project's CSP
header is `style-src 'self'` (no `'unsafe-inline'`) per
`app/__init__.py:723` and the C-02 inline-style migration noted
in `app.css:1296-1305` removed 92 such attributes precisely so
the policy could stay strict. These three slipped back in.

Browsers honour CSP per Level 3: when `style-src-attr` is unset, it
falls back to `style-src`. So `style-src 'self'` blocks inline
`style="..."` attributes on Firefox and Chromium; the styles fail
to apply and the buttons rely on whatever sizing the surrounding
Bootstrap classes provide. The tests in
`tests/test_integration/test_security_headers.py` lock the header
shape; nothing currently locks "no inline style attrs in any
template", so the regression is silent until someone DOM-inspects
a mobile card and notices the missing min-height.

### Recommended fix (estimated effort: 5 minutes)

Replace the three `style="min-height: 44px;"` attributes with a
single CSS class (e.g. `.btn-touch-44`) added to
`app/static/css/app.css` inside the existing mobile media query
block:

```css
@media (max-width: 767.98px) {
  .btn-touch-44 {
    min-height: 44px;
  }
}
```

and replace each button's `style="..."` with
`class="... btn-touch-44 ..."`. Mirror the convention used by
`.mw-px-*` / `.w-px-*` / `.fs-*` utility classes already
established by the C-02 migration in `app.css`.

### Test coverage

The existing CSP tests in
`tests/test_integration/test_security_headers.py` already lock the
header shape. A new test asserting `style=` does not appear in
`_mobile_card_actions.html` would lock the regression; matches the
existing `test_no_inline_style_on_swipe_action` in
`TestMobileSwipeAction` (Commit 9). One-line pattern:

```python
def test_no_inline_style_on_mobile_card_actions(self):
    src = pathlib.Path(
        "app/templates/grid/_mobile_card_actions.html"
    ).read_text()
    assert "style=" not in src
```

### Why defer

Commit 9's stated scope is the swipe-left gesture: CSS for the
revealed button, JS for the touch handlers, macro change to emit
the button. Refactoring three pre-existing inline style attributes
in a sibling partial is a separate refactor with its own review
surface. The inline styles are currently inert (blocked by CSP) so
the 44 px touch target is not currently enforced on the action-bar
buttons -- a visible regression, but not a new one introduced by
this commit.

---

## F-4. Delete deprecated `app/templates/companion/_transaction_card.html`

- **Surfaced during:** Commit 13
  (`refactor(grid): extract grid_view_service + companion uses This Period partial + swipe.js shared`).
- **Status:** closed (commit `96dd07e`,
  `chore(companion): delete orphan _transaction_card.html`).
  ``git rm`` of the 94-line orphan; zero callers since v3 commit 13
  when ``companion/index.html`` adopted the shared
  ``grid/_mobile_this_period.html`` partial.  Two comment-only
  references survive (a design-lineage pointer in
  ``app/templates/grid/_grid_row_macros.html:189`` and the
  ``TestMarkPaidButtonVisibility`` docstring in
  ``tests/test_routes/test_companion_routes.py``); the docstring's
  "(still on disk, no longer reached by the route)" parenthetical
  was rewritten to reference the removal + git history so the
  historical-string explanation continues to read accurately.

### Problem

`app/templates/companion/_transaction_card.html` (94 lines) is no
longer reached by any route after Commit 13.  Companion's
`index.html` was rewritten to
`{% include "grid/_mobile_this_period.html" with context %}` and
the partial in turn calls `render_row_card`; the legacy card's
markup is now produced entirely by the shared macro.

A grep confirms zero callers:

```
grep -rn "_transaction_card" app/ tests/
# (empty)
```

The file remains on disk solely to make Commit 13 easy to revert by
restoring three lines in `companion/index.html`.  Once the new
flow is verified stable, the file is dead weight that future
authors might mistake for live code.

### Recommended fix (estimated effort: 1 minute)

```bash
git rm app/templates/companion/_transaction_card.html
```

Run the full pytest suite afterwards to confirm no test imports or
greps the file by name; the targeted runs in `test_companion_routes.py`
+ `test_companion_guards.py` (90 tests) already pass without
reaching the file post-Commit-13, so deletion is a no-op for the
runtime.

### Test coverage

No new tests required.  The existing companion test suite
(`tests/test_routes/test_companion_routes.py`) asserts on the
post-Commit-13 markup; nothing references `_transaction_card.html`
directly.

### Why defer

Commit 13's plan section H (Rollback notes) explicitly directs
leaving the file in place for rollback ease: "delete in a follow-up
commit or leave as-is for history; this commit just stops
including it."  Following that guidance lets the developer revert
Commit 13 with a single-file edit to `companion/index.html`
restoring the include.  Once the new card flow has shipped
through one or two release cycles without regression, the file
becomes safe to delete in this follow-up.

---

## F-5. Reorder third-party / first-party imports in `app/routes/grid.py`

- **Surfaced during:** Commit 13
  (`refactor(grid): extract grid_view_service + companion uses This Period partial + swipe.js shared`).
- **Status:** closed (commit `26536fe`,
  `style(grid): reorder third-party / first-party imports`).
  Pre-existing pylint warning, not introduced by Commit 13;
  confirmed via `git stash` baseline check against pre-refactor
  `dev`.  Moved ``sqlalchemy.orm.selectinload`` into the
  third-party block and re-anchored
  ``app.utils.auth_helpers.require_owner`` at the alphabetical
  end of the local block; ``grid.py``-local pylint score went
  9.53 -> 9.72.

### Problem

`app/routes/grid.py:18-20` interleaves a third-party import
(`from sqlalchemy.orm import selectinload`) between two first-party
ones (`from app.utils.auth_helpers import require_owner` at
`:17`, then `from app.extensions import db` at `:20`).  Pylint
reports:

```
app/routes/grid.py:18:0: C0411: third party import "sqlalchemy.orm.selectinload"
  should be placed before first party import "app.utils.auth_helpers.require_owner"
  (wrong-import-order)
app/routes/grid.py:20:0: C0412: Imports from package app are not grouped
  (ungrouped-imports)
```

The `docs/coding-standards.md` Python section states: "Three
sections separated by blank lines: standard library, third-party,
local application. Alphabetical within each section."  The
current grid.py order violates the third-party-before-local rule
and the ungrouped-imports rule.

### Recommended fix (estimated effort: 2 minutes)

Move `from sqlalchemy.orm import selectinload` up into the
third-party block (between `flask_login` and the first
`from app.*` line), so the file reads:

```python
import logging
from collections import OrderedDict
from datetime import date
from decimal import Decimal

from flask import Blueprint, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.orm import selectinload

from app.extensions import db
from app.models.scenario import Scenario
from app.models.transaction import Transaction
from app.models.category import Category
from app.models.ref import Status, TransactionType
from app.services import balance_resolver, grid_view_service, pay_period_service
from app.services.account_resolver import resolve_grid_account
from app.services.entry_service import build_entry_sums_dict
from app.services.grid_view_service import RowKey
from app.services.scenario_resolver import get_baseline_scenario
from app.utils.auth_helpers import require_owner
```

Note the secondary reordering: `app.utils.auth_helpers` moves to
its alphabetical position at the end of the local block (since
`utils` sorts after `services`).

### Test coverage

Pylint already catches this.  Running `pylint app/routes/grid.py
--fail-on=E,F` after the fix should show the C0411 and C0412
warnings gone and the score nudging up.  No runtime test changes
required.

### Why defer

Commit 13's scope is the grid_view_service extraction +
companion partial-include + swipe.js shared module.  Reordering
unrelated imports in `grid.py` would mix scope and obscure the
refactor's diff.  Pre-existing warning, no functional impact, fix
as a one-commit chore.

---

## F-6. Decompose `app/routes/grid.py::index` to reduce locals / branches / statements

- **Surfaced during:** Commit 13
  (`refactor(grid): extract grid_view_service + companion uses This Period partial + swipe.js shared`).
- **Status:** closed (commit `9fbd2f8`,
  `refactor(grid): decompose index() into five private helpers`).
  Extracted the five private helpers the plan named
  (`_resolve_grid_context`, `_load_grid_transactions`,
  `_build_grid_balances`, `_build_grid_subtotals`,
  `_build_grid_row_data`); rewrote `index()` as a thin orchestrator
  (~35 lines of logic plus the 24-kwarg `render_template` call).
  Pylint R0914 (33/15), R0912 (15/12), R0915 (54/50) on `index()`
  all cleared; `app/routes/grid.py` module score went 9.72 -> 10.00;
  project-wide score unchanged at 9.59.  Canonical-producer wiring
  preserved -- `balance_resolver.balances_for` still at
  `_build_grid_balances` and `balance_resolver.period_subtotal`
  still at `_build_grid_subtotals`; the three static-guard locks
  (`TestGridPeriodSubtotalCanonical::test_grid_inline_subtotal_loop_removed`,
  `::test_grid_balance_computation_routed_through_resolver`,
  `TestCheckingDetailCanonicalProducer::test_accounts_checking_balance_routed_through_resolver`)
  stay green by construction.  Two small plan deviations folded in
  to clear the orchestrator's pylint counts:
  (1) `_resolve_grid_context` returns a private `_GridContext`
  NamedTuple rather than a bare tuple, so the orchestrator accesses
  fields via attribute (`ctx.scenario`) instead of binding seven
  separate locals from a tuple-unpack;
  (2) `_build_grid_row_data` returns a 5-tuple instead of 4 -- the
  per-period `txn_by_period` index loop was folded into the helper
  to drop two locals (the dict + its loop var) from the orchestrator.
  Plan Section 9 D step 5 explicitly anticipated helper-shape
  adjustment as needed.  Behaviour preserved: 180 `test_grid.py`
  tests pass, full suite 5660 pass (66 s).  Out-of-scope flag in
  `J`: the `txn_by_period` kwarg passed to `render_template` is not
  read by any template under `app/templates/`; the helper builds
  and forwards it for behaviour-equivalence, candidate for a future
  cleanup commit.

### Problem

`app/routes/grid.py::index` (lines 47-285 post-Commit-13) trips:

```
app/routes/grid.py:47:0: R0914: Too many local variables (33/15) (too-many-locals)
app/routes/grid.py:47:0: R0912: Too many branches (15/12) (too-many-branches)
app/routes/grid.py:47:0: R0915: Too many statements (54/50) (too-many-statements)
```

The view orchestrates: scenario resolve, account resolve, period
range computation, transaction query, anchor / balances resolve,
subtotals build, category load + filter, row-source scoping,
row-key + match-dict build, status / transaction-type lookups,
column-size derivation, low-balance-threshold lookup, and finally
`render_template`.  Each step is straight-line code but the
cumulative locals + branches push the function past the project's
quality thresholds.

`coding-standards.md` Python section: "If a function exceeds 50
lines, evaluate decomposition. Functions over 100 lines require
justification."  `index()` is well past both.

### Recommended fix (estimated effort: 30-60 minutes)

Extract sub-orchestrators into private helpers in `grid.py`:

- `_resolve_grid_context(user_id, request_args, settings) -> tuple`
  returning `(scenario, account, num_periods, start_offset,
  current_period, periods, all_periods)` or one of the empty-state
  template responses.
- `_load_grid_transactions(account, scenario, all_periods) ->
  list[Transaction]`.
- `_build_grid_balances(account, scenario, all_periods) -> tuple`
  returning `(balances, stale_anchor_warning, anchor_balance)`.
- `_build_grid_subtotals(account, scenario, periods) -> dict`.
- `_build_grid_row_data(transactions, periods, show_all,
  all_categories) -> tuple` returning
  `(income_row_keys, expense_row_keys, matched_by_row_period,
  entry_sums)`.

`index()` becomes a thin orchestrator that calls each helper and
passes the result to `render_template`.  Locals drop below 15,
branches below 12, statements below 50.

The view's HTTP concerns (request_args parsing, request_context
binding, `render_template` calls) stay in `index()`; the helpers
take plain inputs and return plain outputs (the existing pattern
the project already uses for `app/services/*`).

### Test coverage

Targeted run of `tests/test_routes/test_grid.py` (178 tests) and
`tests/test_routes/test_templates.py` should stay green by
construction; the refactor preserves behaviour by moving
straight-line code into helpers without changing any branch
condition or return value.

Optionally add unit tests for each new helper.  The helpers are
private (`_` prefix) so direct-import tests are acceptable; the
existing `TestGridRowKeyBuilder` pattern in `test_templates.py`
(now post-Commit-13 importing from
`app.services.grid_view_service`) is the model.

### Why defer

Commit 13 is a refactor + UI unification; decomposing `index()`
on top of it would mix scope and obscure the diff.  Commit 13's
extraction already reduced the function's size (lifted ~140 LOC
of helpers into `grid_view_service`), but the orchestration still
trips the thresholds -- a separate cleanup commit is the right
shape.

---

## F-7. Add 44 px min-height floor to `.shekel-scroll-pills .nav-link`

- **Surfaced during:** Commit 16
  (`feat(mobile-settings): sidebar -> shekel-scroll-pills on mobile`).
- **Status:** closed (commit `1725acd`,
  `style(mobile): 44 px floor on .shekel-scroll-pills .nav-link`).
  Pre-existing for the analytics and loan dashboard tab rows;
  Commit 16 surfaces it again by adopting the same class for the
  settings section nav on mobile. Added a
  ``.shekel-scroll-pills .nav-link`` rule inside the existing
  ``@media (max-width: 767.98px)`` block in
  ``app/static/css/app.css`` setting ``min-height: 44px`` plus
  flexbox vertical centering; the pre-existing
  ``white-space: nowrap`` rule outside the media query is
  unchanged.

### Problem

`.shekel-scroll-pills .nav-link` (`app/static/css/app.css:954-956`)
inherits Bootstrap's default `.nav-link` padding of `0.5rem 1rem`.
With Bootstrap's default `1rem` font-size and `1.5` line-height the
rendered height is `8 + 24 + 8 = 40 px`, which is `4 px` short of
the WCAG 2.5.5 AA / Apple HIG 44 px touch-target floor mandated by
the v3 plan's hard rule 7.

The `#mobile-grid .nav-pills .nav-link` rule at
`app/static/css/app.css:825-830` enforces the 44 px floor inside the
mobile-grid tab container but is scoped to `#mobile-grid` only, so
the same class on the analytics page (`analytics/analytics.html:16`),
the loan dashboard (`loan/dashboard.html:21, :323`), and now the
settings page (`settings/dashboard.html:22`) does not inherit it.

### Recommended fix (estimated effort: 5 minutes)

Add a floor inside the existing `@media (max-width: 767.98px)` block
in `app/static/css/app.css`:

```css
@media (max-width: 767.98px) {
  .shekel-scroll-pills .nav-link {
    min-height: 44px;
    display: flex;
    align-items: center;
    justify-content: center;
  }
}
```

This mirrors the existing `#mobile-grid .nav-pills .nav-link` rule
and applies the floor consistently to every scroll-pills consumer.

### Test coverage

No new test required. Visual / DevTools spot check on each consumer
(`/analytics`, any loan dashboard at `/savings/loan/<id>`, `/settings`)
at 375x812 confirms the rendered pill is >= 44 px tall.

### Why defer

Commit 16's stated file scope is
`app/templates/settings/dashboard.html` only. Touching
`app/static/css/app.css` for a pre-existing gap that affects three
unrelated consumers (analytics, loan, settings) is a separate
refactor with its own review surface. The floor gap is inert on
desktop (where pills are not the primary touch surface) and the
new settings pills inherit the same behaviour as the established
analytics and loan dashboard consumers, so the commit does not
introduce a regression -- it widens the surface of a pre-existing
issue.

---

## F-8. Convert `recurrence_cell` macro from string-name comparisons to ref_cache IDs

- **Surfaced during:** Commit 19
  (`feat(mobile-templates): cards on mobile in templates/list.html`).
- **Status:** closed (commit `b6db37f`,
  `refactor(templates): recurrence_cell uses REC_* pattern ID globals`).
  Pre-existing CLAUDE.md "Reference Tables" violation that Commit 19
  re-cited by adopting the macro as the canonical recurrence-label
  producer across both desktop table and mobile card.  Swept both
  files in one commit per this entry's "if `transfers/list.html`
  carries the same pattern" guidance; verified the parallel macro
  carried the identical violation.  Rewired both macros from
  ``pname == 'Every Period'`` to ``rr.pattern_id == REC_EVERY_PERIOD``
  (and the seven sibling patterns), sourcing the integer constants
  from the existing ``app.jinja_globals.register_ref_id_globals``
  registration site rather than adding a new dict-injection context
  processor: ``register_ref_id_globals`` already exposes 40+ bare
  ID globals (``STATUS_PROJECTED``, ``TXN_TYPE_INCOME``, etc.) and
  already had 7 of the 8 ``REC_*`` patterns wired (``REC_MONTHLY``,
  ``REC_QUARTERLY``, ``REC_ANNUAL``, etc.) consumed by
  ``templates/form.html`` and ``transfers/form.html``; the only
  missing constant was ``REC_EVERY_PERIOD``.  Plan drift folded in:
  the plan's D-F design decision claimed
  ``recurrence_pattern_labels`` was "already keyed by enum member"
  -- it is actually a flat string-to-string dict at
  ``app/__init__.py:285-294``, and the plan's recommended
  dict-injection approach would have duplicated infrastructure the
  established convention already provides.  Added
  ``TestRecurrenceCellLock`` with two methods covering both files:
  a no-strings lock (``.name ==`` and ``pname ==`` absent) and a
  positive complement asserting every ``REC_*`` constant appears as
  ``rr.pattern_id == REC_<MEMBER>`` so a future branch-deletion
  regression fails loud instead of silently falling through to the
  ``else`` defensive fallback.  Pylint score unchanged at 9.59/10;
  full suite 5,660 tests green in 67.77s.

### Problem

`app/templates/templates/list.html:21-50` defines a `recurrence_cell`
macro that produces a human-readable label for a transaction template's
recurrence rule. Its body does:

```jinja
{% set pname = rr.pattern.name %}
{% if pname == 'Every Period' %}
  Every paycheck
{% elif pname == 'Every N Periods' %}
  Every {{ rr.interval_n }} paychecks
{% elif pname == 'Monthly' and rr.day_of_month %}
  Monthly (day {{ rr.day_of_month }})
{% elif pname == 'Monthly First' %}
  ...
{% elif pname == 'Quarterly' %}
  ...
{% elif pname == 'Semi-Annual' %}
  ...
{% elif pname == 'Annual' %}
  ...
{% elif pname == 'Once' %}
  One-time
{% else %}
  {{ recurrence_pattern_labels.get(pname, pname|replace('_', ' ')|title) }}
{% endif %}
```

This violates CLAUDE.md "Reference Tables -- IDs for logic, strings
for display only. Enums in `app/enums.py`, cached in `app/ref_cache.py`.
NEVER compare against string `name` columns in Python or Jinja."
(see also `docs/coding-standards.md` Reference Tables section and
the `memory/feedback_id_based_lookups.md` user-feedback note.)

The grep gate
`grep -nE 'recurrence(_pattern)?\.name ==' templates/list.html`
silently passes because the comparisons land on the intermediate
`pname` string variable rather than on `rr.pattern.name` directly --
the gate is a near-miss that should be tightened along with the fix.

The `else`-branch fallback (`recurrence_pattern_labels.get(pname, ...)`)
hints that a centralised label dict already exists somewhere in the
template context; if so, the entire elif chain is dead weight that
can collapse to a single lookup.

### Recommended fix (estimated effort: 30 minutes)

Two steps:

1. **Drive the comparisons off `pattern_id` via `ref_cache`.**
   Replace each `pname == 'Every Period'` with
   `rr.pattern_id == EVERY_PERIOD_ID` where `EVERY_PERIOD_ID` is
   `ref_cache.recurrence_pattern_id(RecurrencePatternEnum.EVERY_PERIOD)`
   resolved once per request and injected into the template context
   (the same pattern `TXN_TYPE_INCOME` already uses in this file at
   line 93). `RecurrencePatternEnum` exists at `app/enums.py:RecurrencePatternEnum`
   and `ref_cache.recurrence_pattern_id(...)` is the established
   resolver (used by `app/routes/templates.py:652, :692`).
2. **Tighten the verification grep.** Update the v3 plan's gate to
   `grep -nE '\.name\s*==\s*[\'"]' templates/list.html` (matches any
   `.name ==` regardless of the receiver name) so future regressions
   on the same shape cannot land silently.

If `transfers/list.html` carries the same pattern (Commit 20 territory),
sweep both files in one commit.

### Test coverage

Targeted `tests/test_routes/test_templates.py` (59 tests) should
stay green by construction -- the refactor is a label-source change
with no rendered-text difference. Add one regression-lock test that
asserts the absence of `.name ==` comparisons in the file:

```python
def test_no_string_name_comparisons_in_recurrence_cell(self):
    src = pathlib.Path(
        "app/templates/templates/list.html"
    ).read_text()
    assert ".name ==" not in src
    assert "pname ==" not in src
```

The two-line assertion locks both the direct form and the
intermediate-variable form that the current gate misses.

### Why defer

Commit 19's stated scope is the mobile card-layout conversion for
`templates/list.html` only. The recurrence-label refactor is a
pre-existing CLAUDE.md violation in a macro Commit 19 only reuses;
fixing it would mix scope (template-layout vs. ref-cache routing)
and extend the diff into the route layer (the context-variable
injection). The macro is rendered identically by both the desktop
table and the new mobile card, so the violation does not get worse
in scope -- it just becomes a more visible candidate now that the
macro is the single source of the recurrence label across both
breakpoints.

---

## F-9. Retire the orphaned `dashboard.mark_paid` route + tighten the now-vacuous paid-bills assertion

- **Surfaced during:** Commit 22
  (`feat(mobile-dashboard): order Bills Due first + (mark-paid removed)`).
- **Status:** closed (commit `5860fa6`,
  `refactor(dashboard): retire dashboard.mark_paid route + tests + cross-refs`).
  Q-1 disposition resolved at Commit 22 as REMOVE (per memory
  ``project_dashboard_redesign_or_remove.md``); the template side
  was done then, the route + tests + cross-refs side closed now.
  Deleted: the ``mark_paid`` route handler in
  ``app/routes/dashboard.py`` plus the now-orphan
  ``_get_owned_transaction`` and ``_txn_to_bill`` helpers and 11
  now-unused imports; the ``_mark_done_schema`` singleton; 13 tests
  across 4 test files (``TestMarkPaid`` x 7, ``TestMarkPaidWithTrackedBill``
  x 1, ``TestDashboardMarkPaidActualAmount`` x 2,
  ``TestDashboardMarkPaidStateMachine`` x 3); 6 comment cross-refs.
  Rewrote the vacuous ``"Already Paid" not in html or "mark-paid-btn"
  not in html`` disjunction at ``test_dashboard.py:194`` to hit the
  ``/dashboard/bills`` HTMX partial directly and assert the paid bill
  name is absent (non-vacuous because the partial renders only the
  upcoming-bills list).  Plan drift folded in: ``MarkPaidSchema``
  never existed (only ``MarkDoneSchema``, shared with the surviving
  ``transactions.mark_done``); the plan's R-6 undercounted the test
  delta by 5 and missed one additional cross-ref at
  ``tests/test_schemas/test_c27_input_validation_sweep.py:10``.
  Pylint score went 9.58 -> 9.59.

### Problem

Commit 22 deleted the mark-paid `<button>` from
`app/templates/dashboard/_bill_row.html` per the user's Q-1 decision.
No template now references `url_for('dashboard.mark_paid', ...)`, but
the route and its supporting cast are still wired:

- `app/routes/dashboard.py:57` -- `mark_paid(txn_id)` endpoint
- `app/routes/dashboard.py:33`-ish -- module-level `_MARK_PAID_SCHEMA`
  singleton (per the existing comment naming `mark_paid`)
- `app/schemas/validation.py:394` -- `MarkPaidSchema` definition
- `tests/test_routes/test_dashboard.py:218-340` -- `TestMarkPaid`
  class, 7 tests POST-ing directly against the route
- `tests/test_routes/test_dashboard_entries.py:538` --
  `test_mark_paid_tracked_returns_paid_row_without_progress` (and
  any siblings)
- `tests/test_routes/test_c27_input_validation_sweep.py:331-335` --
  `MarkPaidSchema` parse-rule sweep
- `tests/test_routes/test_c21_state_machine_broad_rollout.py:6` --
  module docstring naming `dashboard.mark_paid` in the state-machine
  rollout scope
- Cross-references in comments at
  `app/services/transfer_service.py:507`,
  `app/routes/transfers.py:1101`,
  `app/schemas/validation.py:394`,
  and tests at `tests/test_routes/test_transfers.py:905`

Additionally, the assertion at
`tests/test_routes/test_dashboard.py:194` --

```python
assert "Already Paid" not in html or "mark-paid-btn" not in html
```

was a meaningful "paid bills don't show a mark-paid button" check
when the button existed.  With the button removed entirely, the
right operand is always true, so the test passes vacuously regardless
of what the dashboard does with the paid bill.  The test's intent
(verifying that paid bills are not double-presented as actionable)
deserves a non-vacuous form.

### Recommended fix (estimated effort: 1 hour)

Two steps, in this order so the tests can drive the deletion:

1. **Delete the route, schema, and tests.**
   - Drop `mark_paid` and the `_MARK_PAID_SCHEMA` singleton from
     `app/routes/dashboard.py`.
   - Drop `MarkPaidSchema` from `app/schemas/validation.py` (verify
     no remaining importers via `grep -rn 'MarkPaidSchema' app/ tests/`
     before the delete).
   - Drop `TestMarkPaid` from
     `tests/test_routes/test_dashboard.py` (7 tests).
   - Drop `test_mark_paid_tracked_returns_paid_row_without_progress`
     and any siblings from
     `tests/test_routes/test_dashboard_entries.py` (verify the file's
     other tracked-entry tests don't depend on the helper).
   - Remove the `dashboard.mark_paid` row from the
     `test_c27_input_validation_sweep.py` parametrize list.
   - Touch `test_c21_state_machine_broad_rollout.py` module
     docstring + any in-file parametrize entries.
   - Sweep comments in
     `app/services/transfer_service.py:507`,
     `app/routes/transfers.py:1101`,
     `app/schemas/validation.py:394`,
     `tests/test_routes/test_transfers.py:905`
     that name `dashboard.mark_paid` as a comparison point and
     re-anchor them on `transactions.mark_done` (which remains the
     canonical mark-done route per `[[project_dashboard_redesign_or_remove]]`
     audit decision E-23).
2. **Tighten the paid-bills assertion to a non-vacuous form.**
   Replace
   `tests/test_routes/test_dashboard.py:194` with a direct check
   that the paid bill is excluded from the upcoming-bills section
   rather than the disjunction that now self-satisfies. The route
   logic at `app/routes/dashboard.py::_upcoming_bills` (verify
   current line) is the single producer of that section's data;
   the assertion should mirror its filter rather than rely on
   button-presence as a proxy.

### Test coverage

Targeted `tests/test_routes/test_dashboard.py` and
`tests/test_routes/test_dashboard_entries.py` must stay green by
construction (tests being deleted are tests for code being deleted;
the remaining suite still exercises the bills section, the
balance section, the alerts section, etc.). Full pytest run as the
gate.

### Why defer

Commit 22's stated scope is "Delete the mark-paid form from
`_bill_row.html`" per the Q-1 ASK's option (c) wording.  Cascading
into the route, schema, six test files, and four comment-only
back-references would extend the diff well past the surface the
ASK named and into territory (state-machine sweep test docstring,
input-validation sweep parametrize) that touches the audit
infrastructure the financial-calculation remediation depends on.
The orphaned route is inert (no UI calls it; only its own tests
do) and the vacuous assertion is degraded-not-broken, so deferring
to a focused cleanup commit is the correct trade-off.

---

## F-10. Capture DevTools SW cache audit (Section 10 item 7 / Section 11)

- **Surfaced during:** Commit 28
  (`chore(release): mobile v3 full gate + verification appendix`).
- **Status:** open.  Not blocking; the SW invariant is enforced by
  construction (`app/static/sw.js`'s `STATIC_PREFIXES` allow-list +
  the cache-first guard scope cache writes to `/static/*` only) and
  the full pytest suite is green.  The DevTools capture is the
  documented regression check, not a discovery step.

### Problem

`docs/implementation_plan_mobile_v3.md` Section 10 item 7 asks for a
post-Commit-25 DevTools capture (Application -> Cache Storage ->
`shekel-static-v1`) confirming every cached entry begins with
`/static/`.  Commit 28 (this commit's session) elected to defer the
capture because no SW change has landed since Commit 25 and the
invariant is enforced statically.  The appendix in Section 11 of the
plan records the deferral pointing at this entry.

### Recommended capture (5 minutes)

1. Open Firefox (Desktop) at `http://172.32.0.1:5000` (or the
   dev origin).  Hard-reload to register the SW.
2. DevTools (F12) -> Application tab (or "Storage" in Firefox) ->
   Cache Storage -> `shekel-static-v1`.
3. Inspect every entry; assert each `Request URL` starts with
   `/static/`.  Screenshot the panel into
   `tests/manual/screenshots/commit28_sw_cache_audit.png`.
4. Re-run on the iPhone (XS or 16 Plus) in Firefox iOS / Safari
   "Add to Home Screen" install context to confirm the same invariant
   under WebKit.

If any entry is *not* `/static/`-prefixed, treat it as a D-I
regression: open a new follow-up, do not silently fold the audit.

### When to do this

Before any future commit that modifies `app/static/sw.js` (the SW
fetch handler's cache-first branch is the load-bearing piece; a
typo there could cache an HTML response).  Until then the static
analysis (read of `STATIC_PREFIXES` + the cache-first guard) is
sufficient evidence the invariant holds.
