> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Follow-up Remediation -- Commit Prompts

- Companion to: `docs/audits/financial_calculations/remediation_follow_up_plan.md`
- Required reading for every prompt: `docs/audits/financial_calculations/remediation_follow_up_common.md`
- Purpose: one ready-to-paste session prompt per commit (22 total) so each commit can be
  executed in its own fresh session.
- Audience: future Claude Code sessions (and the developer reading what each session was
  asked to do).

## How to use this document

1. Wait until every prerequisite commit listed under "Prereqs on dev" has been merged to
   `dev` (and `main`, via the PR-gated workflow in CLAUDE.md). Each prompt depends only on
   the state of `dev`, not on any prior session context.
2. Start a fresh Claude Code session at the project root with `dev` checked out.
3. Copy the entire fenced block under the commit's heading. Paste it as the first message
   in the new session. Do not edit it.
4. The session will read the canonical plan section for this commit, re-verify against
   current code, do the work, run the gates, and stop with a structured work summary that
   ends by asking whether to commit and push. **No commit or push happens without your
   explicit go-ahead.**
5. After the commit lands on `dev` and CI is green, open a PR `dev` -> `main`. After
   merge, resync `dev`
   (`git fetch origin && git checkout dev && git merge origin/main && git push origin dev`)
   before starting the next prompt.
6. If a session reports drift between the plan and current code, stop and reconcile (edit
   the plan or adjust the prompt) before continuing. The plan is the floor, not a
   free-floating wish list.

The prompts are ordered to match the plan's commit numbering (Section 5 checklist). Read
`remediation_follow_up_plan.md` Section 6 (Dependency Analysis) once before starting; the
prereqs in each prompt below encode it but the picture is easier to hold from the DAG.

---

## Group A -- Documentation, dead code, and foundations

### Commit 1 -- `docs(audit): correct Commit 10 obligations cross-ref (F-4)`

**Prereqs on dev:** none. **Closes:** F-4 (doc drift only).

```text
You are executing Commit 1 of the Shekel financial-calculation audit follow-up remediation
in a fresh session. Work in the project root on the dev branch.

Required reading -- read each in full BEFORE anything else (use @path so they are fetched,
do not summarize from memory or training):
- @docs/audits/financial_calculations/remediation_follow_up_plan.md (Sections 0-6 for
  context; Section 7 "Commit 1" for the A-H specification)
- @docs/audits/financial_calculations/remediation_follow_up_common.md (apply rules and
  work summary format -- mandatory)
- @docs/audits/financial_calculations/remediation_follow_up.md (F-4 entry for the
  documented drift)
- @docs/audits/financial_calculations/remediation_plan.md (Section 9 "Commit 10" -- the
  paragraph to be corrected; and Section 9 "Commit 23" -- where the obligations
  monthly-equivalent aggregator actually landed)
- @app/routes/obligations.py (confirm it uses obligations_aggregator.committed_monthly,
  not balance_resolver.period_subtotal)

Objective: documentation-only correction. The main remediation_plan.md Section 9 Commit 10
prose names app/routes/obligations.py as a target for routing through
balance_resolver.period_subtotal. That is the wrong API for the obligations route, which
aggregates amount_to_monthly across templates (a monthly-equivalent rollup, not a per-
period transaction subtotal). The actual work landed correctly in Commit 23 (E-24 /
HIGH-05) via obligations_aggregator.committed_monthly. Correct the Commit 10 paragraph and
mark F-4 resolved.

Files this commit touches:
- docs/audits/financial_calculations/remediation_plan.md (one paragraph in Section 9
  Commit 10; rewrite the obligations bullet to point at Commit 23 / E-24)
- docs/audits/financial_calculations/remediation_follow_up.md (change F-4 Status line to
  "resolved by Commit 1 of remediation_follow_up_plan.md")

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md (sections "Apply these
rules (every commit)" and "Work summary format"). End the session with the work summary
using labels A through M verbatim.

Specific verification gates for this commit:
- grep -nF "balance_resolver.period_subtotal" docs/audits/financial_calculations/remediation_plan.md
  shows no obligations-related match after your edit.
- grep -nF "balance_resolver.period_subtotal" app/routes/obligations.py returns empty
  (confirms the doc was wrong, not the code).
- The targeted-suite line in the work summary is "n/a (docs-only commit, no test gate
  needed)" -- but still run pylint app/ --fail-on=E,F to confirm no incidental breakage.

If anything is unclear, ASK. Do not change app/routes/obligations.py in this commit.
```

---

### Commit 2 -- `chore(tests): remove dead _skip_user_bootstrap_period flag (F-5)`

**Prereqs on dev:** none. **Closes:** F-5 (dead test-fixture flag).

```text
You are executing Commit 2 of the Shekel financial-calculation audit follow-up remediation
in a fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/audits/financial_calculations/remediation_follow_up_plan.md (Sections 0-6; Section
  7 "Commit 2" A-H)
- @docs/audits/financial_calculations/remediation_follow_up_common.md (rules + work
  summary format -- mandatory)
- @docs/audits/financial_calculations/remediation_follow_up.md (F-5 entry)
- @tests/conftest.py (read lines :800-:900 in full; the bare_user fixture and the
  surrounding context)

Objective: delete the dead `_skip_user_bootstrap_period` global flag from tests/conftest.py
and its two assignments in the bare_user fixture. Project-wide grep shows zero readers --
the flag was intended to suppress an after_insert listener that no longer exists. Removing
it has zero functional impact; the bare_user fixture continues to yield the bare-user-
with-no-period state.

Files this commit touches:
- tests/conftest.py (remove the `global _skip_user_bootstrap_period` declaration and both
  True / False assignments inside bare_user; also remove any module-level
  `_skip_user_bootstrap_period = False` declaration if present)

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md. End with the work
summary using labels A through M verbatim.

Specific verification gates for this commit:
- `grep -rn "_skip_user_bootstrap_period" /home/josh/projects/Shekel/` returns empty.
- `./scripts/test.sh -k "bare_user" -v` -- every test that consumes bare_user still
  passes.
- Full suite green.

If anything is unclear, ASK.
```

---

### Commit 3 -- `chore(utils): delete unused pct_to_decimal helper (F-16)`

**Prereqs on dev:** none. **Closes:** F-16 (dead helper).

```text
You are executing Commit 3 of the Shekel financial-calculation audit follow-up remediation
in a fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/audits/financial_calculations/remediation_follow_up_plan.md (Sections 0-6; Section
  7 "Commit 3" A-H)
- @docs/audits/financial_calculations/remediation_follow_up_common.md
- @docs/audits/financial_calculations/remediation_follow_up.md (F-16 entry)
- @app/utils/formatting.py (current content)
- @tests/test_routes/test_loan.py (find the docstring at ~:419 referencing pct_to_decimal)

Objective: delete app.utils.formatting.pct_to_decimal -- it has zero production callers
after main remediation Commit 24 moved every percent-to-fraction conversion into
Marshmallow @pre_load hooks. Remove the lingering docstring reference in
test_routes/test_loan.py and either delete app/utils/formatting.py outright (if no other
content remains) or narrow it to whatever else lives there.

Files this commit touches:
- app/utils/formatting.py (delete the function and _HUNDRED constant if no other reader;
  delete the file if it becomes empty)
- tests/test_routes/test_loan.py (rewrite the docstring sentence to drop the
  pct_to_decimal reference; cite the current schema-side _normalize_percent_fields helper
  if context is helpful)

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md. End with the work
summary using labels A through M verbatim.

Specific verification gates for this commit:
- `grep -rn "pct_to_decimal" /home/josh/projects/Shekel/` returns empty.
- `grep -rn "from app.utils.formatting" /home/josh/projects/Shekel/` returns empty (or
  only matches for any unrelated remaining symbol).
- pylint app/ --fail-on=E,F clean.
- Full suite green.

If anything is unclear, ASK.
```

---

### Commit 4 -- `refactor(retirement): replace truthiness on financial values (F-11, F-12)`

**Prereqs on dev:** none. **Closes:** F-11 + F-12 (both in
`retirement_dashboard_service.py`).

```text
You are executing Commit 4 of the Shekel financial-calculation audit follow-up remediation
in a fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/audits/financial_calculations/remediation_follow_up_plan.md (Sections 0-6; Section
  7 "Commit 4" A-H)
- @docs/audits/financial_calculations/remediation_follow_up_common.md
- @docs/audits/financial_calculations/remediation_follow_up.md (F-11 and F-12 entries)
- @docs/audits/financial_calculations/08_findings.md (CRIT-04 context -- the truthiness-
  on-financial-values invariant Commit 20 of the main remediation established)
- @app/services/retirement_dashboard_service.py (read in full; the two sites are around
  :385 and :507 but re-grep)

Objective: close two violations of the post-CRIT-04 "no truthiness on financial values, no
truthiness on SQLAlchemy-object existence checks" invariant in
retirement_dashboard_service.py:
- F-11: `bal = proj.get(...) or Decimal("0")` -- replace with an explicit `is None` guard.
- F-12: `if params and projection_periods:` where `params` is an InvestmentParams instance
  -- replace with `if params is not None and projection_periods:`.

Files this commit touches:
- app/services/retirement_dashboard_service.py (both sites; add one-line comment above the
  F-11 site naming the invariant)
- tests/test_services/test_retirement_dashboard_service.py (new test pinning the upstream
  `proj.get` contract -- when proj.get returns Decimal("0") for a real zero-balance
  account, the weighted-return loop includes the account at weight 0; do NOT skip it)

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md. End with the work
summary using labels A through M verbatim.

Specific verification gates for this commit:
- `grep -nF 'or Decimal("0")' app/services/retirement_dashboard_service.py` returns no
  matches in money contexts.
- `grep -nE 'if [a-z_]+ and ' app/services/retirement_dashboard_service.py` shows no
  truthiness gate on a SQLAlchemy object.
- Hand-computed weighted-return test: two accounts ($0 at 7%, $100k at 5%) returns
  ($0 * 0.07 + $100,000 * 0.05) / ($0 + $100,000) = 0.05.
- Targeted test file passes; full suite green.

If anything is unclear, ASK.
```

---

### Commit 5 -- `refactor(services): use canonical MONTHS_PER_YEAR constant (F-15)`

**Prereqs on dev:** none. **Closes:** F-15 (four rate-periodicity sites).

```text
You are executing Commit 5 of the Shekel financial-calculation audit follow-up remediation
in a fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/audits/financial_calculations/remediation_follow_up_plan.md (Sections 0-6; Section
  7 "Commit 5" A-H)
- @docs/audits/financial_calculations/remediation_follow_up_common.md
- @docs/audits/financial_calculations/remediation_follow_up.md (F-15 entry)
- @app/utils/money.py (the canonical MONTHS_PER_YEAR definition)
- @app/services/debt_strategy_service.py (TWELVE constant at ~:33)
- @app/services/escrow_calculator.py (inline Decimal("12") at ~:118)
- @app/services/interest_projection.py (MONTHS_IN_YEAR at ~:48)
- @app/services/loan_resolver.py (inline Decimal("12") at ~:378)

Objective: replace four local Decimal("12") aliases / inline literals in service files
with the canonical `MONTHS_PER_YEAR` constant from app.utils.money. Same numeric value
(Decimal("12")), so there is NO behaviour change. Delete the local TWELVE / MONTHS_IN_YEAR
aliases.

Files this commit touches:
- app/services/debt_strategy_service.py
- app/services/escrow_calculator.py
- app/services/interest_projection.py
- app/services/loan_resolver.py
(Add `from app.utils.money import MONTHS_PER_YEAR` where missing; do not re-import if
already present via a star or grouped import.)

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md. End with the work
summary using labels A through M verbatim.

Specific verification gates for this commit:
- `grep -rnE '(TWELVE|MONTHS_IN_YEAR) = Decimal\("12"\)' app/services/` returns empty.
- `grep -rnF 'Decimal("12")' app/services/` returns at most legitimate sites in
  obligations_aggregator.py (Commit 23 of the main remediation already routed the
  biweekly-to-monthly factor through MONTHS_PER_YEAR; the four rate-periodicity sites are
  now MONTHS_PER_YEAR).
- Targeted suites for each of the four service files pass:
  `./scripts/test.sh tests/test_services/test_debt_strategy_service.py tests/test_services/test_escrow_calculator.py tests/test_services/test_interest_projection.py tests/test_services/test_loan_resolver.py -v`
- Full suite green.

If anything is unclear, ASK.
```

---

### Commit 6 -- `refactor(tests): extract Jinja-globals registration helper + sync missing entries (F-7)`

**Prereqs on dev:** none. **Closes:** F-7 (DRY + drift fix; the conftest list is
**missing 8 entries** that exist in app/__init__.py).

```text
You are executing Commit 6 of the Shekel financial-calculation audit follow-up remediation
in a fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/audits/financial_calculations/remediation_follow_up_plan.md (Sections 0-6; Section
  7 "Commit 6" A-H; note the R-FU-1 scope addition: the lists are out of sync, NOT in sync
  as the F-7 doc claims)
- @docs/audits/financial_calculations/remediation_follow_up_common.md
- @docs/audits/financial_calculations/remediation_follow_up.md (F-7 entry; treat the
  "currently in sync" claim as stale -- verification at plan time showed otherwise)
- @app/__init__.py (read :160-:260 in full; this is the canonical list of 45 globals)
- @tests/conftest.py (read :2080-:2130 in full; this list is missing TIMING_PRE_TAX,
  TIMING_POST_TAX, CALC_PERCENTAGE, CALC_FLAT, GOAL_MODE_FIXED, GOAL_MODE_INCOME_RELATIVE,
  INCOME_UNIT_PAYCHECKS, INCOME_UNIT_MONTHS)
- @app/ref_cache.py and @app/enums.py (ID lookups; the helpers consume these)

Objective: extract the ID-derived Jinja globals registration into a single
app/jinja_globals.py:register_ref_id_globals(app) helper that both create_app() and the
conftest's _refresh_ref_cache_and_jinja_globals call. Closes the DRY violation AND fixes
the eight missing entries in conftest (any template that referenced one of the missing
constants during tests would raise UndefinedError).

Files this commit touches:
- app/jinja_globals.py (new) -- one function register_ref_id_globals(app: Flask) -> None
  with a substantive docstring naming the F-7 invariant. Includes every entry from the
  canonical app/__init__.py list (45 globals).
- app/__init__.py -- replace the inline registration block in create_app() with a call to
  register_ref_id_globals(app).
- tests/conftest.py -- replace the inline registration block in
  _refresh_ref_cache_and_jinja_globals with the same call.

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md. End with the work
summary using labels A through M verbatim.

Specific verification gates for this commit:
- `grep -nF "jinja_env.globals[" app/__init__.py tests/conftest.py` shows only the call
  sites (no inline assignment outside the helper).
- `python -c "from app.jinja_globals import register_ref_id_globals; print(register_ref_id_globals)"`
  imports cleanly.
- A new test renders a route that consumes one of the eight previously-missing constants
  (e.g. an income-goal template referencing INCOME_UNIT_PAYCHECKS) without UndefinedError.
  Pick one from `grep -rn "INCOME_UNIT_PAYCHECKS" app/templates/`; pin the route test to
  assert the rendered HTML contains an expected fragment dependent on the constant.
- A test invokes register_ref_id_globals(app) twice in succession; the second call is a
  no-op (idempotent).
- Full suite green; in particular, every route test that renders income-goal / pay-timing
  / calc-method templates passes.

If anything is unclear, ASK.
```

---

## Group B -- Targeted behavioural fixes

### Commit 7 -- `fix(retirement): reject negative SWR slider override (F-13)`

**Prereqs on dev:** none. **Closes:** F-13 (negative SWR silently collapses to zero).

```text
You are executing Commit 7 of the Shekel financial-calculation audit follow-up remediation
in a fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/audits/financial_calculations/remediation_follow_up_plan.md (Sections 0-6; Section
  7 "Commit 7" A-H; note the locked direction is "reject 422" not "clamp" not "document")
- @docs/audits/financial_calculations/remediation_follow_up_common.md
- @docs/audits/financial_calculations/remediation_follow_up.md (F-13 entry)
- @app/services/retirement_gap_calculator.py (the calculator; keep behaviour unchanged)
- @app/routes/retirement.py (the /gap_analysis route and the inline Decimal("100")
  division that this commit eliminates)
- @app/schemas/validation.py (existing retirement schemas; the post-Commit-24 @pre_load
  pattern for percent normalisation)
- @tests/test_services/test_retirement_gap_calculator.py (lines :309-:311 carry a
  BUG/TODO comment that this commit removes)

Objective: reject negative `swr` slider overrides at the /retirement/gap_analysis route
with a 422 (Marshmallow ValidationError) instead of silently letting the calculator
collapse to zero. The calculator itself is unchanged (defensive depth). The
percent-to-fraction conversion moves into the schema @pre_load hook so the route stops
doing money math (matches the Commit 24 / HIGH-06 convention).

Files this commit touches:
- app/schemas/validation.py (add RetirementGapQuerySchema with swr as Decimal validated
  to Range(min=Decimal("0"), max=Decimal("1")); add a @pre_load hook that divides input
  by 100 to convert percent -> fraction; use the existing _normalize_percent_fields
  pattern if it fits)
- app/routes/retirement.py (route the /gap_analysis query parameters through the new
  schema; remove the inline Decimal("100") division; catch ValidationError and return 422
  with the standard error envelope)
- tests/test_services/test_retirement_gap_calculator.py (remove the BUG/TODO comment
  block at :309-:311; verify test_safe_withdrawal_rate_negative still passes -- the
  calculator semantics are unchanged)
- tests/test_routes/test_retirement.py (new tests pinning the 422-on-negative and the
  happy-path cases)

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md. End with the work
summary using labels A through M verbatim.

Specific verification gates for this commit:
- GET /retirement/gap_analysis?swr=-5 returns 422; response body contains an `swr` field
  error.
- GET /retirement/gap_analysis?swr=0 returns 200; calculator returns
  required_retirement_savings = ZERO (existing behaviour).
- GET /retirement/gap_analysis?swr=4 returns 200 with the standard gap result.
- The existing test_safe_withdrawal_rate_negative calculator test passes unchanged.
- `grep -nF "BUG:" tests/test_services/test_retirement_gap_calculator.py` returns empty.
- Full suite green.

If anything is unclear, ASK. Do not loosen the calculator's `> 0` guard.
```

---

### Commit 8 -- `fix(transfers): defense-in-depth filter on hard_delete_transfer_template (F-14)`

**Prereqs on dev:** none. **Closes:** F-14 (defense-in-depth parity with templates.py).

```text
You are executing Commit 8 of the Shekel financial-calculation audit follow-up remediation
in a fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/audits/financial_calculations/remediation_follow_up_plan.md (Sections 0-6; Section
  7 "Commit 8" A-H)
- @docs/audits/financial_calculations/remediation_follow_up_common.md
- @docs/audits/financial_calculations/remediation_follow_up.md (F-14 entry)
- @app/routes/transfers.py (read in full; the hard_delete_transfer_template route is
  around :620-:680)
- @app/routes/templates.py (read the parallel hard_delete_template route around :620-:650
  for the reference defense-in-depth pattern with notin_(settled_status_ids))
- @app/models/status.py (Status.is_settled boolean) and @app/ref_cache.py
- @app/services/transfer_service.py (delete_transfer signature)
- @docs/audits/financial_calculations/08_findings.md (CRIT-05 context)

Objective: mirror the templates.py defense-in-depth pattern on
hard_delete_transfer_template. Commit 21 of the main remediation already fixed the
predicate `transfer_template_has_paid_history` to filter on Status.is_settled, so the
guard at the route's entry is effective. This commit adds the second layer: the bulk-
delete loop itself filters on `Transaction.status_id.notin_(settled_status_ids)` so that
even if a future regression bypasses the guard, settled transfers and their shadow pairs
survive.

Files this commit touches:
- app/routes/transfers.py::hard_delete_transfer_template (build a settled_status_ids
  scalar subquery from Status.is_settled.is_(True); partition the linked transfers; loop
  delete_transfer(soft=False) only over the non-settled list; surviving settled transfers
  retain their transfer_template_id which is FK ON DELETE SET NULL)
- tests/test_routes/test_transfers.py (new route test that monkey-patches
  transfer_template_has_paid_history to False, POSTs the hard-delete, asserts the settled
  transfer plus its two shadow rows survive with original amounts and statuses)

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md. End with the work
summary using labels A through M verbatim.

Specific verification gates for this commit:
- `grep -nF "notin_(settled_status_ids)" app/routes/transfers.py` returns the new filter
  site.
- Existing test_hard_delete_preserves_shadow_invariant continues to pass.
- New test C8-1 (monkey-patch guard False, post delete, settled transfer + 2 shadows
  survive) passes.
- Non-settled (PROJECTED) transfers are still deleted by the bulk loop.
- Full suite green.

If anything is unclear, ASK.
```

---

### Commit 9 -- `fix(companion): move entry pct derivation to service helper (F-23)`

**Prereqs on dev:** none. **Closes:** F-23 (float cast on Decimal money math in a route).

```text
You are executing Commit 9 of the Shekel financial-calculation audit follow-up remediation
in a fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/audits/financial_calculations/remediation_follow_up_plan.md (Sections 0-6; Section
  7 "Commit 9" A-H)
- @docs/audits/financial_calculations/remediation_follow_up_common.md
- @docs/audits/financial_calculations/remediation_follow_up.md (F-23 entry)
- @app/routes/companion.py (read in full; the float cast is at :53-:57)
- @app/services/entry_service.py (the new helper's home)
- @app/services/dashboard_service.py (the existing _safe_pct_complete reference shape at
  :567-:579)
- @app/templates/companion/ (find the template that consumes the rendered `pct` value;
  verify Decimal string format works as CSS width)

Objective: extract the entry-percent-complete derivation out of the companion route into a
service-layer helper `entry_service.pct_complete(total, target) -> Decimal` mirroring the
existing `_safe_pct_complete` in dashboard_service.py. Eliminates the `float(Decimal)`
cast at the route layer (MED-04 / E-16 standard: money math is service-layer Decimal, not
route-layer float).

Files this commit touches:
- app/services/entry_service.py (add pct_complete function with substantive docstring
  naming the MED-04 / E-16 standard; return Decimal in [0, 100] quantised to 2dp)
- app/routes/companion.py (call the helper; remove the `float(...)` cast)
- tests/test_services/test_entry_service.py (new tests for the helper: normal case,
  clamp-at-100 case, target-zero guard)
- tests/test_routes/test_companion_routes.py (pin the rendered pct to a hand-computed
  Decimal value for at least one fixture)

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md. End with the work
summary using labels A through M verbatim.

Specific verification gates for this commit:
- `grep -nF "float(" app/routes/companion.py` returns no matches in money contexts.
- pct_complete(Decimal("50"), Decimal("100")) == Decimal("50.00") (C9-1).
- pct_complete(Decimal("150"), Decimal("100")) == Decimal("100.00") (clamp, C9-2).
- pct_complete(Decimal("50"), Decimal("0")) == Decimal("0") (guard, C9-3).
- Companion HTML for total $55.50 / target $100.00 renders `55.50` in the progress-bar
  width (C9-4); hand arithmetic: 55.50 / 100 * 100 = 55.50.
- Full suite green.

If anything is unclear, ASK. Verify the template's CSS width handles Decimal-string
format before assuming no template change is needed.
```

---

## Group C -- Calendar

### Commit 10 -- `fix(calendar): per-day filter via balance-contributing predicate (F-3)`

**Prereqs on dev:** none. **Closes:** F-3 (W-065 per-day display drift).

```text
You are executing Commit 10 of the Shekel financial-calculation audit follow-up
remediation in a fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/audits/financial_calculations/remediation_follow_up_plan.md (Sections 0-6; Section
  7 "Commit 10" A-H; the locked semantic is balance-contributing -- Projected + Settled,
  excludes Cancelled + Credit -- NOT Projected-only)
- @docs/audits/financial_calculations/remediation_follow_up_common.md
- @docs/audits/financial_calculations/remediation_follow_up.md (F-3 entry)
- @docs/audits/financial_calculations/08_findings.md (HIGH-02 / W-065 context)
- @app/services/calendar_service.py (read :180-:320 in full;
  _query_transactions_for_range :194-:237 and _assign_transactions_to_days :270-:310)
- @app/utils/balance_predicates.py (balance_contributing_clause at :334-:359 -- the
  canonical predicate; same one the grid uses)
- @app/models/status.py (Status.is_balance_contributing column)

Objective: apply the balance-contributing predicate to the calendar's per-day
classification so Cancelled and Credit transactions no longer inflate day-cell totals.
Settled transactions DO contribute (calendar UX shows realized payments at their settled
date). The calendar day-total intentionally differs from the grid's Projected-only
period-subtotal -- this is the locked Choice-2 semantic.

Files this commit touches:
- app/services/calendar_service.py::_query_transactions_for_range (add the predicate to
  the SQL .filter chain)
- app/services/calendar_service.py::_assign_transactions_to_days (re-apply the predicate
  in the Python loop -- belt-and-suspenders so SQL and Python agree; future regressions
  to the SQL filter alone do not break the day-total math)
- tests/test_services/test_calendar_service.py (re-pin per-day totals against
  hand-computed values for mixed-status fixtures; add a regression-lock test pinning the
  locked predicate)

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md. End with the work
summary using labels A through M verbatim. Re-pinned tests in section D MUST include the
finding ID (F-3 / W-065) and the hand-computed arithmetic.

Specific verification gates for this commit:
- `grep -nF "balance_contributing_clause" app/services/calendar_service.py` returns
  matches at both the SQL filter and the Python loop sites.
- C10-1: day with Projected $500 expense + Settled $200 expense -> day-total expenses
  = $700.00 (both contribute).
- C10-2: same day plus Cancelled $100 + Credit $50 -> day-total expenses still = $700.00
  (excluded).
- C10-3: the corresponding grid period-subtotal for the same data = $500.00 (Projected-
  only). The two surfaces intentionally diverge.
- C10-4: simulated regression (drop the predicate) causes C10-2 to fail with $850.00.
- Targeted calendar suite passes; full suite green.

If anything is unclear, ASK. The locked semantic is balance-contributing
(Projected + Settled), not Projected-only -- do not change the choice.
```

---

### Commit 11 -- `refactor(calendar): raise on unresolvable account/scenario; remove dead branches (F-2)`

**Prereqs on dev:** 10. **Closes:** F-2 (calendar zeroed-fallback dead branches).

```text
You are executing Commit 11 of the Shekel financial-calculation audit follow-up
remediation in a fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/audits/financial_calculations/remediation_follow_up_plan.md (Sections 0-6; Section
  7 "Commit 11" A-H)
- @docs/audits/financial_calculations/remediation_follow_up_common.md
- @docs/audits/financial_calculations/remediation_follow_up.md (F-2 entry)
- @docs/audits/financial_calculations/08_findings.md (CRIT-01 / E-19 -- the "NULL anchor
  unreachable" invariant Commits 3-8 of the main remediation established)
- @app/services/calendar_service.py (read in full; the three short-circuit branches at
  :115-:120 and :158-:162, plus _empty_month at :474 and _empty_year at :490)
- The calendar route file (grep "calendar" app/routes/ to locate); needs an exception
  handler returning 404

Objective: delete the three pre-E-19 short-circuit branches in calendar_service.py that
silently return zeroed MonthSummary / YearOverview objects when the account or scenario
resolver returns None. After Commits 3-8 of the main remediation, those nulls indicate an
upstream defect (e.g. deleted account, missing scenario) -- treating them as zeroed
calendars masks the upstream bug and ships a $0.00 calendar with no error to the user.

Replace each short-circuit with `raise CalendarAccountNotResolvableError(...)` (a new
exception class); the calendar route catches and returns 404 (matches the project's
"404 for both 'not found' and 'not yours'" security rule). Delete the now-orphaned
_empty_month and _empty_year factories.

Files this commit touches:
- app/services/calendar_service.py (define the new exception class; replace the three
  short-circuits with raises; delete _empty_month and _empty_year)
- app/routes/calendar.py (or wherever the route lives; catch the new exception and
  abort(404))
- tests/test_services/test_calendar_service.py (update tests that relied on the implicit
  zeroed return to set up a valid fixture or assert the exception)
- tests/test_routes/test_calendar.py (assert the 404 response when account/scenario is
  missing)

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md. End with the work
summary using labels A through M verbatim.

Specific verification gates for this commit:
- `grep -nF "_empty_month" app/services/calendar_service.py` returns no matches.
- `grep -nF "_empty_year" app/services/calendar_service.py` returns no matches.
- `grep -nF 'Decimal("0")' app/services/calendar_service.py` shows no hardcoded zero
  summaries (other Decimal("0") references for legitimate accumulators are fine).
- C11-1: calendar route returns 404 when resolve_analytics_account returns None.
- C11-2: calendar route returns 404 when the baseline scenario is missing.
- C11-3: calendar route returns 200 with the correct summary for a valid fixture.
- Full suite green.

If anything is unclear, ASK. Do not bring back the zeroed factory under any condition --
the locked direction is "raise; route returns 404."
```

---

## Group D -- Schema reconciliation and destructive migration

### Commit 12 -- `refactor(schemas): unify percent conversion at @pre_load for investment + pension (F-17)`

**Prereqs on dev:** none. **Closes:** F-17 (route-layer percent conversion holdouts).

```text
You are executing Commit 12 of the Shekel financial-calculation audit follow-up
remediation in a fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/audits/financial_calculations/remediation_follow_up_plan.md (Sections 0-6; Section
  7 "Commit 12" A-H)
- @docs/audits/financial_calculations/remediation_follow_up_common.md
- @docs/audits/financial_calculations/remediation_follow_up.md (F-17 entry)
- @docs/audits/financial_calculations/08_findings.md (HIGH-06 / E-28 context)
- @app/schemas/validation.py (read in full -- the Commit-24 _PERCENT_FIELDS pattern,
  _normalize_percent_fields helper, and the carve-out paragraph in the module docstring
  that says "Two pre-existing schemas...")
- @app/routes/investment.py (_convert_percentage_inputs at :319-:337)
- @app/routes/retirement.py (inline Decimal("100") divisions at :117-:118, :213-:214,
  :348-:351)

Objective: move every remaining route-layer percent-to-fraction conversion into the
Marshmallow @pre_load hook so the convention is universal. Affects
InvestmentParamsCreateSchema / InvestmentParamsUpdateSchema (currently converted via
_convert_percentage_inputs in investment.py) and PensionProfileCreateSchema /
PensionProfileUpdateSchema / RetirementSettingsSchema (currently converted via inline
Decimal("100") divisions in retirement.py). Delete the carve-out paragraph from the
schemas module docstring.

Files this commit touches:
- app/schemas/validation.py (add _PERCENT_FIELDS + @pre_load normalize_inputs to the five
  schemas; remove the carve-out paragraph)
- app/routes/investment.py (delete _convert_percentage_inputs and its call sites)
- app/routes/retirement.py (delete the three inline Decimal("100") divisions; the schemas
  now own the conversion)
- tests/test_routes/test_investment.py (add a POST test asserting the stored fraction
  matches the percent input)
- tests/test_routes/test_retirement.py (add POST tests for pension create, pension
  update, retirement settings update -- assert stored fractions)

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md. End with the work
summary using labels A through M verbatim.

Specific verification gates for this commit:
- `grep -nF "_convert_percentage_inputs" app/routes/` returns no matches.
- `grep -nF 'Decimal("100")' app/routes/retirement.py` returns no matches in money-
  conversion contexts.
- `grep -nF "Two pre-existing" app/schemas/validation.py` returns no matches.
- C12-1: POST /investment/<id>/params/update with assumed_return=7.5 stores 0.075.
- C12-2: POST /retirement/pension/create with benefit_multiplier=2.5 stores 0.025.
- C12-3: POST /retirement/settings/update with safe_withdrawal_rate=4 and
  estimated_retirement_tax_rate=22 stores 0.04 and 0.22.
- Full suite green.

If anything is unclear, ASK.
```

---

### Commit 13 -- `fix(schema): add upper-bound CHECK on loan_params.interest_rate (F-18)` -- DESTRUCTIVE

**Prereqs on dev:** none. **Closes:** F-18 (asymmetric storage-tier CHECK).

```text
You are executing Commit 13 of the Shekel financial-calculation audit follow-up
remediation in a fresh session. Work in the project root on the dev branch.

This is a DESTRUCTIVE migration. The developer pre-approved it at the
remediation_follow_up_plan.md plan-time scope review (Section 2 design decisions table:
"F-18 destructive migration -- Included"). Author the migration with the Review: line
named below.

Required reading -- in full:
- @docs/audits/financial_calculations/remediation_follow_up_plan.md (Sections 0-6; Section
  7 "Commit 13" A-H; AND the destructive-migration approval row in Section 2)
- @docs/audits/financial_calculations/remediation_follow_up_common.md
- @docs/audits/financial_calculations/remediation_follow_up.md (F-18 entry)
- @docs/coding-standards.md ("Migrations" section: NOT NULL three-step pattern,
  destructive migration requirements, the Review: line, downgrade requirements)
- @app/models/loan_params.py (read in full; __table_args__ at :44-:72; current CHECK is
  only `interest_rate >= 0` at :63-:66, no upper bound)
- @app/models/interest_params.py (:33-:36 -- the sibling apy CHECK 0..1)
- @app/models/loan_features.py (:44-:47 rate_history CHECK 0..1; :111-:114 escrow
  inflation CHECK)
- @app/schemas/validation.py (LoanParamsCreateSchema's Range(0, 1) -- the application-
  tier guard the storage CHECK will mirror)

Objective: add the database-tier CHECK constraint
`ck_loan_params_interest_rate_upper = interest_rate IS NULL OR interest_rate <= 1` so the
storage tier matches the application-tier Marshmallow Range(0, 1). `IS NULL OR ...`
preserves the E-18 / Commit-15 demotion that made the column nullable. Migration
includes a pre-check that raises if any existing row violates the new bound (defends
staging / disaster-recovery replays against unexpected data).

Files this commit touches:
- app/models/loan_params.py (add the second CheckConstraint to __table_args__)
- migrations/versions/<auto>_add_loan_params_interest_rate_upper_check.py (new)
- tests/test_routes/test_loan.py (assert raw-SQL INSERT with interest_rate = 9.5 raises
  IntegrityError mentioning ck_loan_params_interest_rate_upper)
- Run `python scripts/build_test_template.py` after upgrade to refresh the template.

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md. End with the work
summary using labels A through M verbatim. Section G ("Migrations") MUST include the
upgrade -> downgrade -> upgrade results.

Migration constraints (non-negotiable):
- Add `Review: solo developer, <today's ISO date> (remediation_follow_up_plan.md F-18
  destructive migration, plan-time approved)` to the migration's module-level docstring.
- upgrade() runs a pre-check: SELECT count(*) FROM budget.loan_params WHERE
  interest_rate > 1; raise RuntimeError with the diagnostic SELECT embedded in the
  message if any row violates the new bound. Only then op.create_check_constraint.
- downgrade() is op.drop_constraint('ck_loan_params_interest_rate_upper', 'loan_params',
  type_='check', schema='budget'). Lossless; no special handling.
- Three-direction round-trip: `flask db upgrade` then `flask db downgrade -1` then
  `flask db upgrade` cleanly. Rebuild template after each upgrade.

Specific verification gates for this commit:
- `psql -c "\d+ budget.loan_params"` shows both ck_loan_params_interest_rate (lower)
  and ck_loan_params_interest_rate_upper (upper) constraints.
- C13-1: raw-SQL INSERT with interest_rate = 9.5 raises IntegrityError mentioning
  ck_loan_params_interest_rate_upper.
- C13-2: INSERT with interest_rate = 1.0 succeeds (boundary).
- C13-3: INSERT with interest_rate = NULL succeeds (E-18 demotion preserved).
- C13-4: migration round-trips cleanly.
- After `python scripts/build_test_template.py`, the entrypoint trigger-count health
  check still passes (this migration does not change AUDITED_TABLES).
- Full suite green.

If anything is unclear, ASK. Do not author the migration without the Review: line.
```

---

## Group E -- Engine cleanup

### Commit 14 -- `fix(engine): gate anchor-reset payment recompute on not using_contractual (F-8)`

**Prereqs on dev:** none. **Closes:** F-8 (unreachable engine bug; closes the gap).

```text
You are executing Commit 14 of the Shekel financial-calculation audit follow-up
remediation in a fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/audits/financial_calculations/remediation_follow_up_plan.md (Sections 0-6; Section
  7 "Commit 14" A-H)
- @docs/audits/financial_calculations/remediation_follow_up_common.md
- @docs/audits/financial_calculations/remediation_follow_up.md (F-8 entry; note the bug
  is unreachable today because every production call site passes anchor_balance=None for
  fixed-rate)
- @app/services/amortization_engine.py (read :430-:510 in full; the anchor-reset block is
  at :486-:493; using_contractual is set at :430-:440)
- @app/routes/loan.py (the floor_anchor_bal pattern that protects production today;
  around :531 and :615-:618)
- @app/services/loan_resolver.py (the fixed-rate vs ARM anchor-passing logic)

Objective: gate the anchor-reset payment recompute in
amortization_engine.generate_schedule on `not using_contractual` so the bug becomes
mathematically impossible, not just unreachable. For ARM loans (using_contractual=False)
the anchor reset re-amortizes (correct, unchanged behaviour). For fixed-rate loans
(using_contractual=True) the contractual monthly_payment (set at loop entry) remains in
force through the anchor reset. After this commit, the resolver can safely pass anchor
parameters for fixed-rate loans too, opening the door to the F-21 dispatcher unification
and the Commit-16 main-remediation true-up UX projecting from the corrected balance.

Files this commit touches:
- app/services/amortization_engine.py:486-:493 (two-line gate; add a docstring comment
  naming F-8 and explaining the ARM vs fixed-rate semantics)
- tests/test_services/test_amortization_engine.py (new test for the fixed-rate anchor
  pathway)

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md. End with the work
summary using labels A through M verbatim.

Specific verification gates for this commit:
- `grep -nF "if not using_contractual:" app/services/amortization_engine.py` returns the
  new gate.
- C14-1: fixed-rate $400k @ 6% / 360 months, anchor at origination passed explicitly.
  Every row's payment_amount equals calculate_monthly_payment($400000, 0.06, 360) =
  Decimal("2398.20"). Hand: M = 400000 * (0.06/12) / (1 - (1.005)^-360) =
  2398.2046... -> Decimal("2398.20").
- C14-2: ARM mid-loan anchor still re-amortizes (existing behaviour preserved); pin one
  row's hand-computed payment.
- C14-3: every existing engine test passes unchanged.
- Full suite green.

If anything is unclear, ASK. Do not refactor get_loan_projection or anything else in
amortization_engine -- F-10 is a separate commit (15).
```

---

### Commit 15 -- `refactor(loan): delete dead get_loan_projection / calculate_balances_with_amortization (F-10)`

**Prereqs on dev:** none (independent of 14). **Closes:** F-10 (three engine-internal
readers of demoted columns).

```text
You are executing Commit 15 of the Shekel financial-calculation audit follow-up
remediation in a fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/audits/financial_calculations/remediation_follow_up_plan.md (Sections 0-6; Section
  7 "Commit 15" A-H)
- @docs/audits/financial_calculations/remediation_follow_up_common.md
- @docs/audits/financial_calculations/remediation_follow_up.md (F-10 entry)
- @app/services/amortization_engine.py (get_loan_projection at :913+; LoanProjection
  dataclass)
- @app/services/balance_calculator.py (calculate_balances_with_amortization at :226+)
- @app/services/loan_payment_service.py (compute_contractual_pi at :252+ and
  load_loan_context)
- @app/models/loan_params.py (original_principal is always non-NULL; interest_rate is
  nullable post-E-18 but the Marshmallow schema requires it at insert)
- @tests/test_services/test_amortization_engine.py (TestGetLoanProjection class to delete
  or rewrite)
- @tests/test_services/test_balance_calculator_debt.py (entire file to delete)

Objective: close the three engine-internal readers of the E-18 demoted columns
(LoanParams.current_principal / .interest_rate) that survive after main remediation
Commit 15 routed all display paths through the resolver. Two of the three (get_loan_projection,
calculate_balances_with_amortization) have ZERO production callers and are kept alive
only by their tests; delete them and their tests. The third (compute_contractual_pi) is
still called by load_loan_context; rewrite it to compute the escrow boundary from
original_principal (always non-NULL) and the BASE interest_rate (resolver fallback,
required at insert by the schema), preserving the fixed-rate boundary EXACTLY and the
ARM boundary at marginally less precision (boundary is a heuristic for escrow
subtraction ordering; verified by existing tests).

Files this commit touches:
- app/services/amortization_engine.py (delete get_loan_projection function AND the
  LoanProjection dataclass; update module docstring if it references them)
- app/services/balance_calculator.py (delete calculate_balances_with_amortization;
  update module docstring)
- app/services/loan_payment_service.py::compute_contractual_pi (rewrite to use
  original_principal + base interest_rate)
- tests/test_services/test_amortization_engine.py (delete TestGetLoanProjection; if any
  sibling ARM tests depended on get_loan_projection, rewrite them to drive
  loan_resolver.resolve_loan directly)
- tests/test_services/test_balance_calculator_debt.py (delete the entire file)

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md. End with the work
summary using labels A through M verbatim.

Specific verification gates for this commit:
- `grep -rn "get_loan_projection\|calculate_balances_with_amortization\|LoanProjection" /home/josh/projects/Shekel/`
  returns empty (no source or test reference).
- `grep -rn "\.current_principal" app/ | grep -v migrations | grep -v loan_resolver | grep -v loan_anchor_event | grep -v models/`
  returns empty.
- `grep -rn "\.interest_rate" app/ | grep -v migrations | grep -v loan_resolver | grep -v loan_anchor_event | grep -v models/ | grep -v schemas/`
  returns only the new compute_contractual_pi read and any docstring references.
- C15-1: full engine test suite passes.
- C15-2: compute_contractual_pi for a fixed-rate $400k / 6% / 360m loan returns
  Decimal("2398.20") (same as before refactor; hand: 400000 * 0.005 / (1-(1.005)^-360)
  = 2398.20).
- C15-3: escrow subtraction tests pass unchanged.
- C15-4: loan-resolver integration tests pass unchanged.
- Full suite green.

If anything is unclear, ASK. Do not promote OPT-1 (dropping the demoted columns) in this
commit -- that is explicitly deferred.
```

---

## Group F -- Investment, salary, and loan dispatcher

### Commit 16 -- `fix(year-end): investment projection via contribution timeline (F-19)`

**Prereqs on dev:** none. **Closes:** F-19 (lump-sum transfers treated as periodic).

```text
You are executing Commit 16 of the Shekel financial-calculation audit follow-up
remediation in a fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/audits/financial_calculations/remediation_follow_up_plan.md (Sections 0-6; Section
  7 "Commit 16" A-H; the locked direction is Option 1 -- year-end uses the
  build_contribution_timeline shape the dashboard route already uses)
- @docs/audits/financial_calculations/remediation_follow_up_common.md
- @docs/audits/financial_calculations/remediation_follow_up.md (F-19 entry)
- @app/services/investment_projection.py (calculate_investment_inputs Step 2 averaging at
  :142-:161; do NOT change this function; its other callers depend on the average shape)
- @app/services/growth_engine.py (project_balance signature, confirm it accepts a
  contributions timeline that overrides periodic_contribution)
- @app/services/year_end_summary_service.py (_project_investment_for_year at
  :1031-:1180 -- the consumer to refactor)
- The investment dashboard route file (consumer that already passes
  build_contribution_timeline; reference shape)

Objective: fix the year-end investment projection so a user with a lump-sum settled
transfer (e.g. $23,300 end-of-year 401(k) contribution) plus a recurring deduction does
NOT have the lump sum treated as a per-period contribution. The dashboard already passes
a real-per-period `contributions` timeline (via build_contribution_timeline); the year-
end consumer passes the averaged `periodic_contribution` directly. Mirror the dashboard's
shape in year-end.

Files this commit touches:
- app/services/year_end_summary_service.py::_project_investment_for_year (call
  build_contribution_timeline over the year's pay periods; pass the timeline to
  growth_engine.project_balance)
- tests/test_services/test_year_end_summary_service.py (new fixture: one lump-sum settled
  transfer + one recurring deduction; pin year-end employer/growth totals to
  hand-computed per-period-capped values)

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md. End with the work
summary using labels A through M verbatim.

Specific verification gates for this commit:
- `grep -nF "build_contribution_timeline" app/services/year_end_summary_service.py`
  returns the new call site.
- C16-1: lump-sum $23,300 + recurring $1,500 with $100k salary fixture; hand-computed
  year-end employer match respects the 6%-of-gross per-period cap (~$230.77/period for
  $3,846.15 gross; $230.77 * 0.50 * 25 recurring periods + cap applied to lump-sum
  period = approximate total). Pin the exact computed value with the arithmetic in a
  comment.
- C16-2: recurring-only fixture year-end output is byte-identical pre vs post.
- C16-3: dashboard chart for the same fixture remains byte-identical (already uses the
  timeline).
- Full suite green.

If anything is unclear, ASK. Do not refactor calculate_investment_inputs -- the locked
direction is Option 1 (year-end consumer changes, not the function).
```

---

### Commit 17 -- `refactor(income): canonical raise-aware gross-biweekly helper (F-20)`

**Prereqs on dev:** none (independent of 16, but both touch year_end_summary_service --
land 16 first for diff clarity). **Closes:** F-20 (six off-engine salary consumers).

```text
You are executing Commit 17 of the Shekel financial-calculation audit follow-up
remediation in a fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/audits/financial_calculations/remediation_follow_up_plan.md (Sections 0-6; Section
  7 "Commit 17" A-H; the locked direction is Option 1 -- lift the paycheck-engine call
  into one shared income_service)
- @docs/audits/financial_calculations/remediation_follow_up_common.md
- @docs/audits/financial_calculations/remediation_follow_up.md (F-20 entry; F-032 worked
  example)
- @docs/audits/financial_calculations/08_findings.md (F-032 -- the raise-aware salary
  divergence)
- @app/services/paycheck_calculator.py (calculate_paycheck signature and side effects;
  PaycheckBreakdown.gross_biweekly is the canonical raise-aware value)
- @app/services/savings_dashboard_service.py (the Commit-26 reference pattern; off-engine
  division at :304-:314)
- @app/services/year_end_summary_service.py (_load_salary_gross_biweekly at :2047-:2075)
- @app/services/retirement_dashboard_service.py (off-engine division at :447-:450)
- @app/services/investment_dashboard_service.py (_salary_gross_biweekly at :128-:133)

Objective: introduce app/services/income_service.py with one canonical function
`get_current_gross_biweekly(user_id, scenario, *, as_of=None) -> Decimal` that wraps
paycheck_calculator.calculate_paycheck and returns PaycheckBreakdown.gross_biweekly.
Replace every off-engine `Decimal(str(profile.annual_salary)) / (profile.pay_periods_per_year or 26)`
read in services / routes with a call to the new helper. Users with applicable
SalaryRaise rows see corrected income everywhere (audit's F-032 example: $107,120 vs the
pre-fix $104,000 on a 3% raise).

Files this commit touches:
- app/services/income_service.py (new) -- the canonical helper with substantive docstring
  naming F-20 / F-032
- app/services/savings_dashboard_service.py (replace inline division with helper call)
- app/services/year_end_summary_service.py (either replace _load_salary_gross_biweekly's
  body with a delegation or delete the wrapper if callers can switch directly)
- app/services/retirement_dashboard_service.py (replace inline division)
- app/services/investment_dashboard_service.py (replace _salary_gross_biweekly body)
- tests for each consumer pinning the raise-applicable path (C17-1..C17-4)

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md. End with the work
summary using labels A through M verbatim.

Specific verification gates for this commit:
- Services boundary preserved: `grep -nE '^(from|import)\s+flask\b' app/services/income_service.py`
  returns empty.
- `grep -nE "Decimal\(str\([^)]*annual_salary[^)]*\)\)\s*/" app/services/ app/routes/`
  returns empty outside income_service.py and paycheck_calculator.py.
- C17-1: raise-applicable fixture (annual $104k, 3% raise effective period N). Period
  N+1 gross_biweekly = 104000 * 1.03 / 26 = Decimal("4120.00"). Pre-fix was $4000.00
  for the same call site.
- C17-2: no-raise fixture: post == pre (byte-identical).
- C17-3: no-active-profile returns Decimal("0").
- C17-4: integration test pins one raise-applicable scenario across savings DTI,
  retirement gap, investment projection, year-end employer match -- all four read the
  same engine-derived value.
- Full suite green.

If anything is unclear, ASK. Read calculate_paycheck's exact signature before writing the
helper; do not assume.
```

---

### Commit 18 -- `refactor(investment): extract shared deduction-loader + projection-inputs helpers (F-22)`

**Prereqs on dev:** 17. **Closes:** F-22 (R0801 duplicates across three dashboards).

```text
You are executing Commit 18 of the Shekel financial-calculation audit follow-up
remediation in a fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/audits/financial_calculations/remediation_follow_up_plan.md (Sections 0-6; Section
  7 "Commit 18" A-H; the locked direction is Option B -- single
  build_investment_projection_inputs helper that closes both duplicates)
- @docs/audits/financial_calculations/remediation_follow_up_common.md
- @docs/audits/financial_calculations/remediation_follow_up.md (F-22 entry)
- @app/services/investment_dashboard_service.py (_load_deductions_for_account at
  :146-:153; kwargs splat at :258-:263)
- @app/services/retirement_dashboard_service.py (same shape at :416-:422 and :518-:525)
- @app/services/savings_dashboard_service.py (same shape at :289-:295 and :564-:569)
- @app/services/investment_projection.py (calculate_investment_inputs signature; the
  natural home for the new helpers)
- @app/services/income_service.py (Commit 17 -- the helper may use this)

Objective: eliminate the R0801 duplicate-code warning by introducing two shared helpers in
investment_projection.py (or a sibling module):
- `load_active_deductions_for_account(user_id, account_id) -> list[PaycheckDeduction]`
  for the byte-identical filter query duplicated three times.
- `build_investment_projection_inputs(account, params, user_id, all_periods, current_period, salary_gross_biweekly) -> InvestmentInputs`
  for the kwargs splat duplicated three times.

Each consumer dashboard switches from inline construction to the shared helper.

Files this commit touches:
- app/services/investment_projection.py (or a new app/services/projection_inputs.py if
  cleaner) -- add the two helpers
- app/services/investment_dashboard_service.py (delete _load_deductions_for_account;
  route through shared helpers)
- app/services/retirement_dashboard_service.py (delete duplicate query; route through
  shared helpers)
- app/services/savings_dashboard_service.py (delete duplicate query; route through
  shared helpers)
- tests pinning the helper behaviour; consumers' existing test suites should pass
  unchanged

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md. End with the work
summary using labels A through M verbatim.

Specific verification gates for this commit:
- `pylint --disable=all --enable=R0801 app/services/investment_dashboard_service.py app/services/retirement_dashboard_service.py app/services/savings_dashboard_service.py`
  shows no duplicate matching the deductions query or the kwargs splat.
- `grep -nF "_load_deductions_for_account" app/services/` returns only the helper
  definition.
- `grep -nE "salary_gross_biweekly=salary_gross_biweekly,\s*\)" app/services/` returns
  matches only at the helper site.
- C18-1: helper-equivalence lock test -- fixture with one deduction + one contribution;
  shared helper returns the same InvestmentInputs as the previous inline construction.
- C18-2: each dashboard's existing test suite passes unchanged (byte-identical outputs).
- Full suite green.

If anything is unclear, ASK. Preserve the consumers' per-dashboard adaptation of
deductions (only the loading step is centralised).
```

---

### Commit 19 -- `refactor(loan): unify period-balance dispatcher; period-end-keyed canonical (F-21)`

**Prereqs on dev:** none (independent of 17/18, but both touch year_end_summary_service
-- land 17/18 first for diff clarity). **Closes:** F-21 (dual loan-balance derivation).

```text
You are executing Commit 19 of the Shekel financial-calculation audit follow-up
remediation in a fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/audits/financial_calculations/remediation_follow_up_plan.md (Sections 0-6; Section
  7 "Commit 19" A-H; the locked canonical is period-end-keyed -- the year-end semantic)
- @docs/audits/financial_calculations/remediation_follow_up_common.md
- @docs/audits/financial_calculations/remediation_follow_up.md (F-21 entry)
- @app/services/savings_dashboard_service.py (target-month-first walk at :467-:475 --
  this is the divergent derivation being retired)
- @app/services/year_end_summary_service.py (_schedule_to_period_balance_map at
  :1572-:1612 -- the canonical implementation to move into a shared module;
  _get_account_balance_map at :2087 -- the consumer)
- @app/services/account_projection.py (existing shared module for cross-dashboard loan/
  investment helpers; the new dispatcher's home)

Objective: unify the two divergent loan-period-balance derivations into one shared
dispatcher `compute_loan_period_balance_map(schedule, periods, original_principal)` in
account_projection.py. Move the existing year-end implementation verbatim (renaming the
function). The savings dashboard's target-month-first walk is replaced by a call to the
dispatcher plus a small period-lookup helper. Locked canonical is period-end-keyed --
the savings 3/6/12-month projected balances will move slightly to the year-end values.

Files this commit touches:
- app/services/account_projection.py (new compute_loan_period_balance_map function with
  substantive docstring naming F-21 and the period-end-keyed semantic)
- app/services/savings_dashboard_service.py:445-:475 (replace the inline walk with a
  call to compute_loan_period_balance_map; resolve the period containing each target
  month)
- app/services/year_end_summary_service.py (delete the local
  _schedule_to_period_balance_map; update _get_account_balance_map to call the shared
  dispatcher)
- tests/test_services/test_savings_dashboard_service.py (re-pin the 3/6/12-month
  projected balances for one loan fixture with the hand-computed period-end balances in
  a comment naming F-21)
- tests/test_services/test_year_end_summary_service.py (assert the year-end debt-progress
  output is byte-identical -- the dispatcher is a pure extraction here)

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md. End with the work
summary using labels A through M verbatim. Section D ("Re-pinned tests") MUST include the
F-21 finding ID and the hand-computed arithmetic for the savings-dashboard re-pin.

Specific verification gates for this commit:
- `grep -nF "compute_loan_period_balance_map" app/services/` returns the new definition
  and both consumer sites.
- `grep -nF "_schedule_to_period_balance_map" app/services/` returns no live matches
  (only updated-comment references, if any).
- C19-1: fixed-rate $400k mortgage fixture at 6% / 360m, current_balance $395k.
  Hand-compute 3/6/12-month projected balances under the period-end-keyed semantic.
  Pin with arithmetic in a comment.
- C19-2: year-end debt-progress output unchanged (byte-identical).
- C19-3: cross-page fixture's loan path produces consistent values across savings +
  year-end (the F-21 motivating goal).
- Full suite green.

If anything is unclear, ASK. Do not change the canonical semantic from period-end-keyed.
```

---

## Group G -- Test infrastructure hardening and large refactor

### Commit 20 -- `test(routes): static guard against bypass of balance_resolver (F-6)`

**Prereqs on dev:** 19 (so the guard locks the final shape). **Closes:** F-6 (gap in
cross-page equality lock).

```text
You are executing Commit 20 of the Shekel financial-calculation audit follow-up
remediation in a fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/audits/financial_calculations/remediation_follow_up_plan.md (Sections 0-6; Section
  7 "Commit 20" A-H; the locked direction is Option (a) -- static guard, not HTML
  parsing)
- @docs/audits/financial_calculations/remediation_follow_up_common.md
- @docs/audits/financial_calculations/remediation_follow_up.md (F-6 entry)
- @tests/test_routes/test_grid.py (find test_grid_inline_subtotal_loop_removed around
  :3794 -- the reference shape)
- @app/routes/grid.py (confirm it currently calls balance_resolver.balances_for)
- @app/routes/accounts.py OR app/routes/accounts/detail.py (post-Commit-21 path); read
  to identify the checking-detail balance computation site

Objective: add two static-grep guard tests that lock the grid and the /accounts checking
routes against silently bypassing balance_resolver. Models the existing
test_grid_inline_subtotal_loop_removed. The cross-page balance-equality lock (Commit 11
of the main remediation) cannot catch this kind of regression because its readers
re-run balance_resolver themselves; this commit closes the gap.

Files this commit touches:
- tests/test_routes/test_grid.py (new test
  test_grid_balance_computation_routed_through_resolver)
- tests/test_routes/test_accounts.py (new test
  test_accounts_checking_balance_routed_through_resolver; if Commit 21 has landed and
  the file moved, path becomes app/routes/accounts/detail.py with the test still in
  test_routes/test_accounts.py)

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md. End with the work
summary using labels A through M verbatim.

Specific verification gates for this commit:
- The new grid guard fires when balance_resolver.balances_for is removed from
  app/routes/grid.py (verify by temporarily removing and re-running the test; restore).
- The new accounts guard fires when balance_resolver.balances_for is removed from the
  checking-detail route (same verification).
- Both tests pass on current main (no false positive).
- The existing test_grid_inline_subtotal_loop_removed still passes (no overlap).
- Full suite green.

If anything is unclear, ASK. The forbidden-pattern list in each guard must be specific
to known regression shapes -- avoid overly broad regex that would catch legitimate uses.
```

---

### Commit 21 -- `refactor(routes): split accounts.py into per-sub-domain modules (F-1)`

**Prereqs on dev:** none. **Closes:** F-1 (1,511-line file violates 1,000-line ceiling).

```text
You are executing Commit 21 of the Shekel financial-calculation audit follow-up
remediation in a fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/audits/financial_calculations/remediation_follow_up_plan.md (Sections 0-6; Section
  7 "Commit 21" A-H; the locked direction is Option A -- single blueprint, file split by
  import; NO url_for changes)
- @docs/audits/financial_calculations/remediation_follow_up_common.md
- @docs/audits/financial_calculations/remediation_follow_up.md (F-1 entry)
- @app/routes/accounts.py (read in FULL -- 1,511 lines; map each endpoint to its
  banner-delimited sub-domain)
- @app/services/anchor_service.py (R-7 extraction -- the apply_anchor_true_up helper that
  both anchor endpoints already call; confirms the highest-value DRY win is already in
  place)
- @app/utils/auth_helpers.py (the ownership-helpers pattern; the new account_validation
  helpers will live alongside)

Objective: mechanical split of app/routes/accounts.py into a package with five
sub-modules (Option A). Single accounts_bp blueprint preserved; every URL unchanged
(zero url_for edits across templates / JS / tests). Shared helpers and Marshmallow
schema singletons move to app/utils/account_validation.py.

Files this commit touches:
- app/routes/accounts/__init__.py (new) -- declares accounts_bp, imports each sub-module
  for side-effect registration
- app/routes/accounts/crud.py (new) -- list_accounts, new_account, create_account,
  edit_account, update_account, archive_account, unarchive_account,
  hard_delete_account
- app/routes/accounts/anchor.py (new) -- inline_anchor_update / _form / _display, true_up
  / anchor_form / anchor_display
- app/routes/accounts/types.py (new) -- create_account_type, update_account_type,
  delete_account_type
- app/routes/accounts/detail.py (new) -- interest_detail, update_interest_params,
  checking_detail
- app/utils/account_validation.py (new) -- _visible_account_types, _owned_account_type,
  _validate_update_account, _account_type_is_visible, the six Marshmallow schema
  singletons
- app/routes/accounts.py (DELETED; replaced by the package)
- app/__init__.py if it does `from app.routes.accounts import accounts_bp` and the
  package needs an import-path change (likely none -- Python treats package the same)

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md. End with the work
summary using labels A through M verbatim.

Specific verification gates for this commit:
- `wc -l app/routes/accounts/*.py` shows every file under 1,000 lines.
- `pylint app/routes/accounts*` shows no C0302 warnings.
- `grep -rn "url_for(\"accounts\." app/templates/ app/static/ tests/` returns the same
  matches as before the split (every URL still resolves).
- The existing tests/test_routes/test_accounts.py (137 tests) passes unchanged.
- The Commit-20 F-6 static guard for the checking-detail route still bites against the
  new file path (app/routes/accounts/detail.py); update its file-path reference if the
  guard hard-codes the old path.
- Full suite green.

If anything is unclear, ASK. Do not change any URL, route function name, or behaviour --
this is a pure mechanical refactor.
```

---

## Group H -- Final gate

### Commit 22 -- `chore(release): follow-up final gate`

**Prereqs on dev:** 1 through 21. **Closes:** the follow-up plan; sweeps
remediation_follow_up.md statuses.

```text
You are executing Commit 22 of the Shekel financial-calculation audit follow-up
remediation in a fresh session. Work in the project root on the dev branch. This is the
final gate.

Required reading -- in full:
- @docs/audits/financial_calculations/remediation_follow_up_plan.md (Sections 0-6;
  Section 7 "Commit 22" A-H; Section 8 -- the symptom walkthrough to verify)
- @docs/audits/financial_calculations/remediation_follow_up_common.md
- @docs/audits/financial_calculations/remediation_follow_up.md (every F-N entry to be
  swept)

Objective: bookkeeping commit. Confirms every commit landed cleanly, runs the full
acceptance gate, and updates remediation_follow_up.md to mark each F-N item resolved by
its closing commit. No code changes.

Files this commit touches:
- docs/audits/financial_calculations/remediation_follow_up.md (one Status line per
  closed F-N item; mechanical sweep)

Apply the rules in
@docs/audits/financial_calculations/remediation_follow_up_common.md. End with the work
summary using labels A through M verbatim.

Gate checklist (every step must pass before committing):
1. `python scripts/build_test_template.py` (Commit 13's migration changed the schema;
   rebuild required).
2. `./scripts/test.sh` -- ends in `N passed`, zero failed/errors/xfailed. Capture the
   final summary line; include in the commit body and in section E of the work summary.
3. `pylint app/ --fail-on=E,F` -- clean, no new warnings vs baseline.
4. `pylint --disable=all --enable=R0801 app/services/investment_dashboard_service.py app/services/retirement_dashboard_service.py app/services/savings_dashboard_service.py`
   shows no duplicate matching F-22's targets.
5. `flask db upgrade` -> `flask db downgrade -1` -> `flask db upgrade` for the Commit-13
   migration -- clean both directions.
6. The cross-page invariant (main remediation Commit 11), the ARM-window stability lock
   (main remediation Commit 13), and the new F-6 static guards (Commit 20 here) all
   green.
7. Sweep remediation_follow_up.md: for each F-N item closed by a commit here, change
   `**Status:** not started` to
   `**Status:** resolved by Commit N of remediation_follow_up_plan.md`. F-9 keeps its
   existing "resolved by Commit 15" reference to the main remediation. F-4's status
   should read "resolved by Commit 1 of remediation_follow_up_plan.md" (set by Commit 1
   here; verify it actually was set).
8. `git status` shows only the docs file changed.

Specific verification gates for this commit:
- Walk the symptom paths in the dev server: open /calendar for a mixed-status fixture
  user (F-3), submit a negative SWR slider (F-13), compare savings 3/6/12-month loan
  numbers against year-end for the same loan (F-21), view a companion progress bar
  (F-23).
- Confirm remediation_follow_up.md is consistent: every F-N item except F-9 should
  reference a follow-up plan commit; F-9 references main remediation Commit 15.
- After this commit lands, the main remediation chain's Commit 37 (final gate from
  remediation_plan.md) can run against a fully resolved tree.

If anything is unclear, ASK. Do not edit anything other than the docs sweep file in
section 7 -- this commit is gate + bookkeeping, no code.
```
