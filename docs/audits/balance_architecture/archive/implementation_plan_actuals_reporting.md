> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Implementation plan: Actuals reporting (Build-Order Step 5)

Date: 2026-07-03. Branch: `feat/actuals-reporting` (worktree off `dev` at `3273907f`).
Companion to `level1_level2_scope_and_fitness.md` (the architecture of record; this is Build-Order
Step 5, the final built step -- "6. Stop.") and to
`adversarial_review_balance_architecture_2026-07-02.md`, whose open items M3 (reader-contract
half), L9 (display-timezone tax attribution, decided 2026-07-03), R9 (this step), and F-1 (the
`from app.models import Posting` fence blind spot) are closed here.

This plan was adversarially reviewed BEFORE approval (a second pass attacking the draft against
live code). Its CRITICAL finding -- a period-granular anchor walk that contradicted the engine's
moment-of-assertion anchor semantics -- is fixed in Section 3; its MEDIUM findings (blast-radius
enumeration, the non-loan liability sign rule, the account-type-change guard) and LOWs are folded
in throughout. Developer approval: 2026-07-03.

## Status banner (as-built ledger; updated per commit)

- C1 (L9 helper + Schedule-A switch) -- DONE (`e7c8b2e0`); counterfactual-verified boundary tests
  at the helper, the loan reader, and the year-end hybrid; review-clean.
- C2 (ref rows) -- DONE (`08900504`): `account_opening`/`account_trueup` sources + `anchor_equity`
  kind (migration `a4c8e2f6b1d3`, round-tripped up/down/up on a template clone); dual-seeded;
  enum-driven parity tests pass unchanged. Also: `TEST_TEMPLATE_DATABASE` override wired through
  `tests/conftest.py` + `scripts/test.sh` + a `.env` fallback in `scripts/build_test_template.py`
  (per the commit's adversarial review: the builder must resolve the SAME name the runner clones,
  and the runner's grep-miss under `set -euo pipefail` must not abort -- both fixed and proven), so
  a parallel checkout with a different migration head builds and clones its OWN test template.
  This branch uses `shekel_test_template_step5` via the untracked `.env`, leaving the shared
  template untouched for the concurrently-active salary-rebuild session.
- C3 (index re-key + lookup hardening) -- DONE (`083c9655`): `uq_ledger_accounts_account` ->
  `uq_ledger_accounts_account_kind (account_id, kind_id)` (migration `b7d9f3a1c5e8`, `Review:`
  line, fail-loud twin guard on downgrade; round-tripped up/down/up on a template clone); the two
  LINKED-kind lookup filters (`posting_reads._ledger_account_for`,
  `ledger_account_service.create_ledger_account_for_account`); nine-kind model taxonomy +
  display-rule caveat + CASCADE paragraph updated; `TestPartialUnique` re-keyed with a
  twin-coexists/second-twin-rejected case. Targeted 120 + the three oracles 56, all green.
- C4 (anchor-equity chart resolver) -- DONE (`cfb1ed10`):
  `ledger_account_service.get_or_create_anchor_equity_account` + `_load_non_loan_account` (the
  inverse of the loan loader: rejects AMORTIZING targets so a loan's linked ledger never gains a
  twin -- the C6-checklist guarantee); Equity class, name snapshot, idempotent under the re-keyed
  unique; 8 new tests incl. the C3-hardening coexistence proof (hook + `_ledger_account_for` keep
  resolving the LINKED row beside a live twin).
- C5 (walk + reconcile, pure) -- DONE (`8c2e9f9e`): `app/services/_posting_reconcile.py` (the loan
  package's `_common` graduated to a services-level shared module and grew the pieces both anchor
  reconciles must agree on: `delta_legs` / `summed_posting_legs` / `account_owner_id` +
  `posted_correction_legs` / `merge_target_legs` / `emit_anchor_correction_entry`, so the two
  correction families share ONE definition of the correction-entry shape and the cross-file
  `duplicate-code` gate stays structurally quiet; loan package re-pointed, `_common` deleted).
  New `account_posting_service/{_walk,_anchors,_sync}.py`: the MOMENT-granular walk (anchor facts
  by `(created_at, id)`, first row = OPENING; source facts read back from the linked ledger in
  three partitions -- transaction-linked by CURRENT `paid_at`, transfer-linked by the income
  shadow's, residue by entry-period start, each falling back to period-start-midnight-UTC;
  inclusive `<=` tie; nonzero-net linkage misses fail loud); the reconcile keyed
  `(source_kind_id, entry_date)` with the history row's OWN period (no period-resolution failure
  mode; posted-only stale keys take the period of what they reverse -- R2); sync entry points
  (per-scenario, all-scenarios = baseline UNION posted-scenario set with a loud baseline-less
  skip, per-user resync excluding loans structurally via `has_amortization IS FALSE`); the walk
  REFUSES amortizing accounts (ValueError) and every sync treats them as a documented no-op.
  23 new unit tests (CRITICAL-1 moment partition, inclusive same-instant tie, pre/post-assertion
  and NULL-`paid_at` attribution, transfer shadow attribution, revert drop-out + self-heal,
  same-day merge landing on the later value, zero-delta books nothing / mints no twin,
  liability ledger-native sign, loan no-op everywhere, baseline-less loud-skip + recovery,
  empty-target key reversal, posted-only stale-key reversal R2-attributed);
  `_LEDGER_IMPORT_TOKENS` gains `_posting_reconcile` (the M-2 coverage guard caught the new
  reader exactly as designed).  Per the commit's adversarial review (no CRITICAL/HIGH; extraction
  verified byte-identical incl. description strings): the transaction source partition gained
  `transfer_id IS NULL` so the three loaders provably PARTITION the linked ledger (a hypothetical
  dual-linked entry would have double-counted), the transfer loader fails loud on a duplicate
  active income shadow (was last-row-wins), and the two defensive reconcile paths the review
  flagged as untested are pinned by the two reversal tests above.  278 targeted green;
  `pylint app/` 10.00.
- C6 (lifecycle wiring + oracle supersession) -- DONE: all seven Section-3.3
  chokepoints live (`create_account` all-scenarios sync after the pairing;
  `apply_anchor_true_up` inside its outcome-translating try; the
  `update_account` guarded callable now also re-classes + resyncs on a type
  change; `reset_pay_periods` per-user resync after the R7 loan resync;
  the effect-time self-heal at BOTH `posting_service` sync tails;
  `create_baseline` per-user recovery resync; the type-change guard in
  `account_validation` + `routes/accounts/types.py` -- extended beyond the
  plan to also refuse a CLASS-crossing `category_id` edit on a custom type,
  the same M2 vector, and allowed crossings on empty ledgers re-snapshot the
  linked row's class via the new
  `ledger_account_service.sync_linked_ledger_class`).  Registration
  (`auth_service`) and both seed fixtures reorder baseline-before-account so
  "production users get a baseline at registration" is true at the moment it
  matters; `_drop_seed_user_bootstrap` resyncs the openings its period
  delete cascades.  **Self-heal predicate (deliberate divergence from
  Section 3.3 point 5):** the resync fires iff the earliest EMITTED delta
  entry's `entry_date` (midnight UTC) is at-or-before the account's latest
  assertion instant -- the reversal side inherits the OLD attribution's
  civil date (R2), so this covers the revert of an early-settled
  future-period source, which the planned `min(current attribution,
  period-start)` form provably misses (its current attribution AND period
  start both sit after the anchor).  Day-granular over-fire is an
  idempotent no-op walk.  **Structural fallout fixed at the root:** the
  balanced-write primitives (`_PostingLeg` / `_emit_balanced_entry` /
  `_utc_civil_date`) moved to a new leaf `app/services/_posting_write.py`
  (posting_service re-exports; the account package now imports only
  `posting_reads` + the leaf), breaking the
  posting_service <-> account_posting_service pylint cycle the `_bp.py`
  way; the frozen Step-2/Step-3 backfill builders' bare `account_id` joins
  to `ledger_accounts` gained `name IS NULL` linked-row pins (chain-safe --
  `kind_id` does not exist at their revisions) because the anchor-equity
  twin otherwise fans the join out and double/quadruple-posts on any re-run
  against a current schema (caught by the backfill-parity oracle).
  Enumerated Section-5 test updates landed: changes-only invariants ->
  absolute (`ledger == opening anchor + settled effects`), the
  kind-agnostic oracle helpers gained LINKED filters (plus a shared
  `tests/_test_helpers.linked_ledger_account`),
  `create_settled_cash_transaction` gained the pinned-`paid_at`-BEFORE-emission
  parameter (mirroring the transfer helper, so entry dates and walk
  attribution agree), lock-reason tests flip to LEDGER_POSTINGS with
  zero-anchor ACCOUNT_ANCHOR companions, hard-delete tests split into
  $0-deletable vs Guard-5-archived pairs, and the loan boundary-migration
  kind sweep excludes the account-correction sources it now legitimately
  shares kinds with.  New wiring tests: unprompted create/true-up/self-heal
  end-to-end, reset re-post, create_baseline recovery, and the four
  type-guard route cases.  Full suite (run alone) 7008 passed;
  `pylint app/` 10.00 clean.  The review's surviving findings (M2
  settled-transfer attribution seam, M1 scenario-creation sync, the C1
  timeliness note, the L1 transitional downgrade hazard) are scheduled as
  follow-up commits F1-F3 at the foot of Section 6; L1 closes inside C7.
- C7 (backfill + boundary migration) -- DONE (`c9f2e6a4b1d8` migration +
  service + deploy hook + suite): `account_posting_service.backfill_all_account_anchor_postings`
  sweeps every non-loan account across all owners
  (`_all_non_loan_account_ids`) through the SAME go-forward
  `sync_account_anchor_postings_all_scenarios` (backfill == go-forward by
  construction), so a backfilled correction is identical to a go-forward one.
  DRY-refactored to mirror the loan package: a `_non_loan_accounts_id_query`
  base builder feeds both the all-owners enumerator and the existing per-user
  one, and a shared `_reconcile_account_ids` loop backs both the backfill and
  `resync_user_account_anchor_postings` (the per-user resync now delegates,
  behavior-preserving); exported from `__init__`.  Deploy hook
  `scripts/init_database.backfill_all_account_anchor_postings_after_migration`
  (self-contained rollback + `ref_cache.init` + backfill + commit, mirroring
  the loan hook) wired into `__main__` after the loan backfill.  The data
  boundary migration `c9f2e6a4b1d8` (down_revision `b7d9f3a1c5e8`, the new
  head; mirror of `f3d6b1a8c2e4`): upgrade is a documented no-op; downgrade
  deletes `account_opening`/`account_trueup` entries (legs cascade) then
  `anchor_equity` ledger accounts by kind, resolving ref ids by name with
  fail-loud guards.  This CLOSES the C6-to-C7 transitional downgrade hazard
  (L1): because the chain is C7 above C3 (`b7d9f3a1c5e8`) above C2
  (`a4c8e2f6b1d3`), a downgrade first removes the corrections + `anchor_equity`
  twins, so C3's twin guard passes and C2's RESTRICT deletes of the ref rows do
  not jam -- verified EXECUTABLE up/down/up on a prod-schema clone carrying a
  real seeded `anchor_equity` row (C7 cleared it -> C3 guard saw 0 twins -> C2
  ref delete clean).  It also makes true the promise
  `test_loan_posting_backfill.py`'s kind sweep relies on (the account-correction
  sources downgrade first in the linear chain).  New oracle-adjacent suite
  `test_posting_ledger_account_backfill.py` (14): posts a cleared opening;
  restores the opening leaving the settled cash entry intact; opening + true-up
  on a multi-anchor account (the `account_trueup` path); a $0-anchor books
  nothing (no entry, no twin, stays hard-deletable); idempotent / no-double-post
  x2; coverage (all accounts across owners with a loan structurally excluded,
  every account, every scenario); the deploy-hook commit observed from a
  separate connection; the revision pair; downgrade removes the corrections +
  twins while keeping the Step-2 cash entry AND the disjoint loan genesis; the
  downgrade-source guard.  `load_init_database_module` and a kind-scoped
  `ledger_account_of_kind` were promoted to `tests/_test_helpers.py` (the loan
  suite re-pointed; `linked_ledger_account` now delegates).  Per the commit's
  adversarial review (clean -- no CRITICAL/HIGH/MEDIUM; migration reversibility
  re-verified independently by the reviewer): the two LOW test-only items were
  folded in (the `ledger_account_of_kind` dedup + the true-up / $0 coverage);
  the remaining like-shaped `_ledger_of_kind` / `_correction_entries` locals in
  `test_account_posting_service.py` (C5) can later delegate to the same shared
  helpers (out of C7 scope).  Targeted 14 + loan backfill 17 + 325 across the
  `linked_ledger_account` users (both reconciliation oracles included) green;
  `pylint app/ scripts/` 10.00 on the touched files (the migration carries only
  the standard Alembic-idiom findings, identical to the shipped loan boundary
  migration and outside the `pylint app/` gate); single head `c9f2e6a4b1d8`.
- F1 (settled-transfer attribution seam) / F3 (timeliness display-tz) -- DONE
  on this branch after C13 (two separate commits); F2 (scenario-creation sync)
  stays pinned to R8.  See Section 6, "Follow-up commits", for the as-built
  record.
- C8 (write-side oracle) -- DONE:
  `tests/test_integration/test_posting_ledger_account_anchor_reconciliation.py`
  (14 tests, 1336 lines).  The ABSOLUTE invariant per non-loan account
  (`linked ledger == latest anchor + SUM(current net of settled sources
  attributed STRICTLY AFTER the latest assertion instant)`), enforced by a
  production-wide sweep `_assert_account_anchors_reconcile` that is a genuine
  THREE-TABLE second opinion: the ledger side reads `account_postings`
  (LINKED-kind-filtered so the `anchor_equity` twin cannot cancel the correction
  pairwise), the source side reads `transactions`
  (`_independent_post_assertion_source_effect`, cash + transfer-shadow effect,
  the `> latest` instant filter restated independently), the anchor side reads
  `account_anchor_history` (`_latest_assertion`, max `(created_at, id)`) -- none
  reuses the walk / reconcile.  Cases: CRITICAL-1 pre-absorb / post-ride
  (moment partition, three-way agreement); the exact-tie boundary (a source at
  the SAME instant as the assertion is absorbed -- pins the walk's inclusive
  `<=` that a strict `<` mutant otherwise survives); ledger-through-each-
  assertion-instant (distinct-civil-day as-of ladder, since every linked entry's
  `entry_date` is the civil date of its attribution instant); transfer source
  on both shadow polarities; revert-after-true-up self-heal (asserts the
  true-up net re-based to -150, not merely the total); pre-anchor NULL-`paid_at`
  absorption (opening delta grows to 700); same-day true-up merge; a
  NEGATIVELY-anchored Credit Card (ledger-native sign, no `-abs`); zero-delta
  books nothing; scenario + owner isolation; backfill == go-forward (clear via
  the boundary migration teardown, re-derive, sweep ties); two non-vacuity
  injections (tamper the latest anchor -> the real sweep raises on the "latest
  anchor" message; inject one leg -> trial balance != 0).  Trueups are staged at
  PINNED `created_at` (`_assert_balance_at` + the all-scenarios sync, the C5
  affordance) so the moment partition is deterministic -- `apply_anchor_true_up`
  stamps `now()`, which cannot be placed between two synthetic settles; the
  chokepoint itself is covered by `test_account_posting_service.py`.  Per the
  commit's adversarial `code-reviewer` pass (MUTATION-tested: perturbing every
  opening/true-up delta by +0.01 fails 12/13, mis-attributing cash sources by
  period-start fails 7/13): its one CONFIRMED gap (the untested `<=`/`<` tie)
  and two LOWs (a weak revert sub-assertion, the same-UTC-day self-heal caveat)
  are folded in; the fix was independently mutation-verified to fail under `<`
  and pass under `<=`.  14 targeted green; monetary-precision checker clean;
  tests are out of the `pylint app/` gate (unchanged, still 10.00) -- no `app/`
  code touched.
- C9 (reporting service) -- DONE: new package
  `app/services/ledger_report_service/` (`_types.py` frozen shapes
  `StatementWindow` / `StatementLine` / `StatementSection` / `TrialBalanceTieOut`
  / `IncomeStatementReport` / `BalanceSheetReport`; `_attribution.py` the shared
  read core -- `dated_account_nets` over the three-bucket partition
  (transaction-linked `transaction_id IS NOT NULL AND transfer_id IS NULL`,
  transfer-linked by income shadow, sourceless-correction positive allowlist;
  residue dropped whole), plus `load_chart` / `ledger_account_label` (kind-
  branched, orphan-safe) / `statement_class_ids` / `present_natural` /
  `section_lines` / `build_section`; `_income_statement.py`
  `compute_income_statement` -- pay-period path (direct `pay_period_id` group)
  and calendar path (display-tz attribution filtered by date); `_balance_sheet.py`
  `compute_balance_sheet` -- fold `<= as_of`, sections by class, derived retained
  earnings (C-5), two-part tie-out (presented `assets == liabilities + equity`
  AND mechanical `ledger_net == 0`, `in_balance` requires both)).  Baseline-only;
  `None` scenario -> empty report / green tie-out.  17 hand-computed service
  tests (`tests/test_services/test_ledger_report_service.py`): month/year/pay-
  period windows, the L9 8:05pm-ET Dec-31 boundary, validation + empty report,
  transfers-absent-from-income + present-on-balance-sheet, seed opening tie-out,
  income/expense -> retained earnings, a negatively-anchored liability signing
  POSITIVE (no `-abs`), the as-of fold boundary, live-rename vs orphaned-category
  labels, and the residue-drop (asserted on the account VALUE, not the tie-out,
  which would pass vacuously).  Fixtures respect moment-of-assertion absorption
  (a settle dated before origination is absorbed into the opening; ride-on-top
  settles use `_RIDES_ON_TOP` / full-position sheets use a far-future `as_of`).
  `pylint app/` 10.00 (two one-sided `duplicate-code` disables with rationale on
  the new-code side: `StatementWindow` vs `VarianceWindow`, and the income-shadow
  query mirroring the write walk -- shipped write-side code untouched).  Adversarial
  `code-reviewer` pass CLEAN (no financial-correctness defect; verified the
  partition covers every live entry once, whole-source dating keeps the tie-out
  an identity, the sign/RE algebra, the loan_payment path reaching the income
  statement via the transaction bucket).  Review follow-throughs carried to C10:
  the route MUST IDOR-check `period_id` via `_validate_owned_or_abort` (the
  service reads the period for its LABEL only, un-scoped, exactly like
  `budget_variance_service`; the money queries are user-scoped so a foreign
  period yields an empty report, but the label would leak that period's dates
  without the route guard).  The loan interest/escrow + articulation coverage
  stays deferred to C13's `test_posting_ledger_statements.py` per the commit
  sequence.
- C10 (routes + templates + tabs) -- DONE: two new `/analytics` pills
  (Income Statement, Balance Sheet) plus their HTMX handlers.
  `income_statement_tab` mirrors `variance_tab` exactly -- IDOR-validates
  `period_id` at the boundary BEFORE the shared parser reads it (the
  `_window_label` un-scoped period read is the same F-098 label-leak vector),
  then range-clamps month/year (the `calendar_tab`/`year_end_tab` convention)
  so a crafted out-of-range window cannot reach `date()`/`monthrange()` in the
  service and 500.  `_resolve_variance_params` was generalized to
  `_resolve_window_params` (shared parse, each tab builds its own window value
  object; behavior-preserving for variance -- verified no other callers).
  `balance_sheet_tab` parses `as_of` via `_resolve_as_of_param` (ISO parse,
  garbage/absent -> today, clamped to [2000-01-01, today]; no DB read, no IDOR
  vector).  Templates `_income_statement.html` (Income/Expense sections +
  Net Income, window toggle + selectors cloned from `_variance.html`) and
  `_balance_sheet.html` (Assets/Liabilities/Equity section-card macro, the
  two-part tie-out footer with success/danger badge, and a `has_content`
  predicate that `rejectattr('ledger_account_id', 'none')`-drops the derived
  Retained Earnings line so a no-baseline user renders the empty state).  No
  new JS (CSP holds).  CSV is DEFERRED to C11 (no `format` branch, no export
  button yet) per the commit sequence.  Route tests: `TestIncomeStatementTab`
  (auth, HTMX render, empty state, no-periods fallback, pay-period posted
  content via `create_settled_cash_transaction`, month/year labels, the
  out-of-range clamp) + `TestBalanceSheetTab` (auth, render, posted content +
  green tie-out with the real-clock opening excluded whole, before-opening
  empty, future/garbage as-of clamp) + `TestIncomeStatementTabPeriodIdOwnership`
  (own 200, cross-user 404, strftime-label no-leak, nonexistent 404, month-
  window defense-in-depth); the page/auth tab lists updated four -> six.
  Per the commit's adversarial `code-reviewer` pass: no CRITICAL/HIGH/MEDIUM
  (the `has_content` predicate proven correct in both directions off the
  double-entry identity; IDOR ordering and the clamps traced against every
  query-string path); its four LOW/nits folded in (stale test docstring, the
  explicit `rejectattr` idiom, the shared-helper section header, the
  strftime-format leak assertion).  167 targeted green; `pylint app/` 10.00.
  - C10 follow-up (post-commit, developer-requested): the calendar-field
    clamp was lifted from `income_statement_tab` into the shared
    `_resolve_window_params` so BOTH window tabs get it from ONE place
    (DRY; the inline income-statement clamp was removed).  This ALSO closed
    a pre-existing `variance_tab` out-of-range 500 that the C10 review
    surfaced: `?window=month&month=13&year=99999` reached
    `calendar.monthrange()` unclamped and raised (`calendar_tab` /
    `year_end_tab` already clamped -- `variance_tab` was the lone outlier).
    The clamp is a no-op for in-range defaults and for `pay_period` windows
    (month/year stay None), so variance is behavior-preserving on every
    real input.  `test_variance_out_of_range_month_clamped` guards it (the
    200 proves the month clamp; the `2100` label, absent from the bounded
    year dropdown, proves the year clamp).  168 targeted green;
    `pylint app/` 10.00.

- C11 (CSV export) -- DONE: `csv_export_service.export_income_statement_csv` /
  `export_balance_sheet_csv` + a shared `_add_statement_section` writer (both
  statements present a `StatementSection` identically, so one helper serves the
  income statement's Income/Expense and the balance sheet's
  Assets/Liabilities/Equity; mirrors the bracketed multi-section layout of
  `export_year_end_csv`).  Income statement = title (window label) + Income +
  Expense sections + Net Income; balance sheet = title (as-of ISO date) +
  Assets/Liabilities/Equity (the Equity section carrying the report's derived
  Retained Earnings line) + a Trial Balance section (Assets, Liabilities +
  Equity, Ledger Net, In Balance) so an out-of-balance ledger is visible in the
  download, not hidden.  Line labels (user category/account names) route through
  `_safe` (CSV-injection CWE-1236); amounts through `_dec` (Decimal-only, no
  `-abs`, faithful to the reader-contract C-4 natural sign the readers already
  applied).  Routes: a `format=csv` branch on both tabs AFTER the report build
  and (income statement) AFTER the boundary `_validate_owned_or_abort(period_id)`
  guard, so a cross-user period CSV 404s before the victim's `start_date` reaches
  the filename; `_variance_csv_filename` generalized to
  `_window_csv_filename(prefix, ...)` shared by variance + income statement
  (byte-identical output for the variance caller; the F-098 route comment + the
  three `test_c30_analytics_ownership` docstrings re-pointed).  Balance-sheet
  filename `balance_sheet_<as_of>.csv` (as_of parsed from the caller's own query
  arg -- no DB read, no IDOR vector).  Templates gained CSV links mirroring
  `_variance.html` (`download`, current window / as-of preserved; `url_for`
  omits the None calendar fields on a pay-period window, the proven variance
  pattern).  Tests: service-level Fake-driven exports (sections/totals/net,
  empty report, negative net, out-of-balance tie-out, natural-signed liability,
  formula-injection) + real report->CSV route tests (export, content pinned to
  EXACT rows `Checking,500.00` / `Retained Earnings,500.00` / `Net Income,1700.00`,
  filename, has-button) + a CSV-path cross-user 404 asserting no
  `Content-Disposition`.  Per the commit's adversarial `code-reviewer` pass: no
  CRITICAL/HIGH/MEDIUM and no money- or security-wrong defect (injection posture,
  IDOR ordering, `_date`-on-`date` ISO, byte-identical variance filename, and the
  no-`-abs` sign faithfulness each independently verified); its one tightness
  note (a bare `"500.00"` balance-sheet assertion that Retained Earnings also
  matched) folded in as the exact-row pins above.  88 targeted green (service +
  `TestCsvExport` + the ownership file); `pylint app/` 10.00.
- C12 (enforcement: W9908 + F-1) -- DONE: new checker
  `tools/pylint/shekel_checkers/ledger_model_fence.py`
  (`ShekelLedgerModelFenceChecker`, message W9908 `shekel-ledger-model-bypass`),
  registered in the package `__init__` (register + `__all__` + rule docstring).
  It is an IMPORT fence on TWO axes, reusing the fail-closed
  `_module_in_allowlist`: the NAME axis flags `Posting`/`JournalEntry`/
  `LedgerAccount` imported from ANYWHERE (the `from app.models import Posting`
  F-1 re-export, the defining submodule, a relative path, a laundering
  re-export), and the MODULE axis flags binding a defining submodule
  (`from app.models.journal_entry import <anything>`,
  `from app.models import journal_entry` -- the submodule bound by name off the
  package -- and the plain `import app.models.journal_entry` via `visit_import`).
  `visit_importfrom` is three priority-ordered branches (submodule -> app.models
  name/leaf -> universal class name) so each name reports at most once (no
  double-report; traced on all five shapes). **Allowlist deviation from Section
  4.4 (correct as-built):** the plan's allowlist predates the C6 commit that
  created `app/services/_posting_write.py`, so `app.services._posting_write`
  (a real importer of the write primitives) was ADDED; grep-verified complete
  against every `app/` importer, and `pylint app/ scripts/` both hold 10.00 with
  ZERO W9908 violations. **Design improvement over the plan (from the C12
  adversarial `code-reviewer` pass, findings H1/M1):** the plan spec'd only the
  module-path + `from app.models import <Class>` shapes, which left
  `from app.models import journal_entry` (submodule-by-name -- a shape the loan
  oracle's OWN detector already treats as ledger-reaching, so production would
  have been WEAKER than the test-side fence) and relative CLASS imports
  reachable; both are closed by the name axis + the app.models leaf-name branch.
  The two accepted residual boundaries (bare `import app.models` + attribute
  access; a RELATIVE submodule-by-name `from ..models import journal_entry`) are
  non-idioms (cross-package imports are absolute, relative imports are
  intra-package only) and are DOCUMENTED in the module docstring + W9908 help,
  not falsely claimed closed. Gate lockstep: `shekel-ledger-model-bypass`
  appended to `_CANONICAL_FAIL_ON` and every executable occurrence -- `ci.yml`
  (four: the comment the regex matches + app/ + scripts/ + checker-package
  steps), `.pre-commit-config.yaml` (three hooks), `post-edit-python.sh` (the
  `--enable` line) -- plus the prose echoes (CLAUDE.md x2, `.pylintrc`, the ci.yml
  and post-edit checker enumerations); `test_fail_on_gate_consistency` (bijection
  with the registry + every gate location) green. **Test-side F-1 closure:** the
  loan oracle's two import-fence detectors (`_imports_a_ledger_model`,
  `_ledger_imports_in_source`) plus a new `_LEDGER_MODEL_CLASS_NAMES` now also
  match `from app.models import Posting/JournalEntry/LedgerAccount`, and the
  negative control (`test_import_fence_flags_a_ledger_import`) proves BOTH
  detectors close the blind spot (with a no-false-positive check on a non-ledger
  `app.models` import). **Pre-existing M-2 gap found + fixed:** the coverage
  guard `test_ledger_import_tokens_cover_every_ledger_reader` was ALREADY RED on
  committed HEAD -- C9's `ledger_report_service` imports the ledger models but was
  never added to `_LEDGER_IMPORT_TOKENS` (it would first have surfaced at the
  C13 full-suite gate); added the `ledger_report_service` token (the resolver
  stack does not import it, so `test_resolver_stack_imports_no_ledger_module` is
  not made a false pass). 133 checker/gate unit tests (25 new W9908 cases + the
  gate-consistency pair) and 5 loan-oracle resolver-fence tests green; the three
  pylint floors (app/, scripts/, checker package) 10.00; W9906's reader-name list
  untouched. Out-of-scope note left for the developer: `.claude/commands/
  standards.md`'s prose `--fail-on` echo is independently stale (it predates
  `shekel-transaction-status-bypass`); not a gate location, deliberately left.
- C13 (statements oracle + docs close-out + full suite) -- DONE: new read-side
  oracle `tests/test_integration/test_posting_ledger_statements.py` (15 tests,
  the FOURTH reconciliation oracle, read-side sibling of the C8 write oracle).
  Non-tautological the same three ways the sibling oracles are: hand-computed
  literals for every line / section total / net income / tie-out figure; an
  INDEPENDENT balance-sheet re-derivation (`_independent_bs`) that groups
  `account_postings` by ledger-account CLASS directly (a different path than the
  reader's per-source attribution) and signs each class from a test-local
  debit-normal set (NOT the ref-cache flag the reader signs by), retained
  earnings derived here; and structural invariants the readers must satisfy
  regardless of any single figure.  Coverage: a rich multi-account fixture
  (category income/expense, the Uncategorized fallback, a negatively-anchored
  Liability charge, a transfer) hand-computed on BOTH statements; a loan
  payment's interest + escrow reaching the income statement as Expense lines
  (500.00 / 100.00) while its cash legs never do, the loan LINKED signing
  POSITIVE as a Liability (99600.00) and the loan `equity_opening` a big
  negative Equity line (-100000.00); the accounting identity `A = L + E` and the
  two-part tie-out at FIVE as-of dates -- THREE placed EXACTLY on an event day
  so the inclusive `<= as_of` fold is pinned (a `< as_of` mutant is caught), two
  straddling a true-up assertion inside one anchor period (the balance-sheet
  moment-partition case); ARTICULATION (income net + windowed equity corrections
  == equity delta between the bounding year-end sheets, relating THREE
  independently-obtained quantities); period-vs-calendar agreement on an aligned
  fixture; a reverted source and CROSS-YEAR hard-delete residue both netting to
  zero and dropped from every window (the residue-drop's cross-year rationale);
  the L9 8:05pm-ET Dec-31 boundary, the NULL-`paid_at` period-start fallback, and
  an early-settled future-period source (the C-2 vs C-3 divergence); live-rename
  / orphan / equity-twin-snapshot labels; scenario + owner isolation; and an
  injected-leg tie-out non-vacuity proof (`ledger_net` off zero, `in_balance`
  False, flushed-not-committed).  Proven NON-VACUOUS by mutation (each verified
  to fail then reverted): dropping the reader's credit-normal negation fails the
  rich + loan cases, flipping the retained-earnings sign fails articulation +
  rich, and the `< as_of` fold mutant fails the as-of ladder (which is why the
  ladder was strengthened to place three as-of dates exactly on event days).
  `pylint` on the test file 9.92 -- only the C0302 line-count and the two W9908
  ledger-model-import findings the sibling C8 oracle also carries (test files
  are outside the `pylint app/` gate).  Docs close-out landed: M3's
  reader-contract half and R9 annotated as closed by this arc, a new dated
  header note in `adversarial_review_balance_architecture_2026-07-02.md`, and the
  Step-5 section added to `level1_level2_scope_and_fitness.md`.  The full-suite
  run surfaced a latent DISPLAY-TIMEZONE bug that the C6 test
  `test_checking_detail_shows_anchor_date` catches only at the UTC/Eastern day
  boundary: the account-detail hero caption rendered the anchor "as of" date from
  `AnchorPoint.as_of_date`, which `balance_resolver.py` derives as the UTC-day of
  the origination `created_at` (correctly UTC for the anchor-dedup index), so a
  late-evening-Eastern anchor showed on the NEXT UTC day -- exactly the L9
  wall-clock class Step 5 codified.  Fixed at the presentation boundary (the
  established "store UTC, display Eastern" rule): `AnchorPoint` now also carries
  the anchor event INSTANT (`created_at`), `routes/accounts/detail.py` passes it,
  and `cash_detail.html` renders it via the `local_datetime` Jinja filter
  (matching the `grid.html` anchor caption); `as_of_date` stays UTC for anchor
  logic.  Three tests moved to the new contract: the immutability constructor
  gains `created_at`; the context-contract test asserts the event instant, not
  the UTC `as_of_date`; and the caption end-to-end test now computes its expected
  string from the anchor's `created_at` via `to_display_date` rather than
  `date.today()`.  That last change is REQUIRED, not cosmetic -- a focused
  adversarial `code-reviewer` pass on the fix caught (HIGH) that leaving the test
  on `date.today()` (the PROCESS timezone) would INVERT the flake onto CI: the
  caption now renders Eastern, so a UTC CI runner would fail the `lint-and-test`
  merge gate every day in the 00:00-04:00 UTC window.  Deriving both the caption
  and the expected string from the same `created_at` makes the assertion
  timezone- and midnight-race-independent, verified passing under BOTH the
  Eastern dev box and `TZ=UTC`.  The reviewer's other notes (conversion
  correctness, `as_of_date` semantics unchanged, the two constructors traced, the
  non-vacuity of the context test) were clean; its one LOW (the `as_of_date`
  docstring overclaimed a live logic consumer -- none remain after the route
  moved to `created_at`) is folded in.  Final gate: full suite (run alone)
  **7107 passed** under BOTH the dev (Eastern) and `TZ=UTC` (CI-equivalent)
  timezones; `pylint app/ scripts/` 10.00 (`balance_resolver.py` now sits at
  exactly its 1000-line cap -- a module split is a recorded follow-up, mirroring
  the `transfer_service.py` R10 note).

Gates for every commit: targeted tests green + `pylint` 10.00 on touched files + an adversarial
`code-reviewer` pass on the staged diff BEFORE committing; migrations tested up AND down; the full
suite (run alone) is the final gate in C13.

---

## 1. What Step 5 delivers

1. **The trial balance closes app-wide.** Every NON-loan account (checking, savings, investment,
   property, interest-bearing) posts an OPENING equity correction for its earliest
   `AccountAnchorHistory` row and a TRUE-UP correction per later row, mirroring the shipped loan
   genesis pattern. After this, every linked ledger sums to an ABSOLUTE balance and
   `assets == liabilities + equity` is checkable end to end. An anchor assertion is a FACT;
   modeled growth/appreciation/interest between assertions is a derivation and is never posted
   (Option D's fact-versus-derivation line, unchanged).
2. **Three reports on the confirmed ledger**: an income statement (pay-period AND calendar
   month/year windows), a balance sheet (as-of date) with a trial-balance tie-out footer, and CSV
   export -- two new tabs on `/analytics`.
3. **The reader contract** (Section 2) -- the ledger-wide attribution rule M3 required deciding
   once, recorded durably and implemented by every reporting reader, including the L9
   display-timezone rule. The year-end Schedule-A mortgage-interest figure switches to the same
   basis.
4. **Enforcement**: a new W9908 `shekel-ledger-model-bypass` import fence for the ledger models,
   and the test-side F-1 detector closure.

Non-goals: NO cash read switch (app balance screens stay on the `balance_at` seam; the engine
remains the projection authority; the stale-anchor warning is unchanged); M6/R8 multi-scenario
gaps stay deferred (reports read the baseline scenario only); no charts on the report tabs; no
category-group accordion nesting in v1.

### What the ledger comes to MEAN for cash

The engine computes only PROJECTED balances: settled items are excluded everywhere because the
anchor is assumed to already reflect them (`balance_calculator.py:13-14, 103-107`), and a settle
without a fresh true-up merely raises the stale-anchor warning. The app therefore has NO computed
"confirmed cash balance" today -- the anchor itself is the closest thing. Step 5's ledger DEFINES
it: **confirmed balance = the latest anchor assertion + settled facts recorded after that
assertion moment.** At every assertion moment the ledger equals the asserted bank truth; between
assertions it extends forward with settled facts. A later true-up whose delta is $0 means the app
tracked reality perfectly; a non-zero delta is the checking "deliberate cheat" made visible as an
explicit equity adjustment. This is the loan-anchor discipline generalized, not a new invention.

---

## 2. The reader contract (normative; M3's open half)

Every Step-5+ reporting reader follows these rules. Recorded here as the durable rule M3
(`adversarial_review_balance_architecture_2026-07-02.md:352-379`) required.

- **C-1 Whole-entry inclusion.** A reader includes or excludes journal entries whole (in practice
  whole SOURCES, which is stronger), never lone legs. Every entry sums to zero (the deferred
  balanced trigger), so any window's included-leg sum is exactly zero and
  `assets = liabilities + equity` (with derived retained earnings) is an identity, not a
  computation that can drift.
- **C-2 Pay-period windows** filter `JournalEntry.pay_period_id` directly. The R2 storage rule
  (reversal/delta entries carry the pay period of the postings they reverse) already makes
  per-period nets honest with no reader-side compensation.
- **C-3 Calendar windows and as-of dates** group entries by SOURCE and attribute each source's
  per-ledger-account NET to the source's CURRENT paid date, converted to the DISPLAY-timezone
  (America/New_York) civil date -- the decided L9 rule -- falling back to the source's pay period
  `start_date` when `paid_at` is NULL:
  - transaction-linked entries (sources `transaction` and `loan_payment`, both carrying
    `transaction_id`): by `Transaction.paid_at`;
  - transfer-linked entries: by the transfer's INCOME shadow's `paid_at` (Transfer Invariant 3
    mirrors `paid_at` onto both shadows; `posting_service._entry_date` already dates transfer
    entries off exactly this shadow);
  - sourceless corrections (`loan_opening` / `loan_trueup` / `account_opening` /
    `account_trueup`): by `entry_date`;
  - hard-delete residue (an FK-bearing source kind whose concrete FK was SET-NULLed): DROPPED, as
    whole entries -- each sums to zero, so the identity survives the drop exactly; the
    reverse-before-delete discipline nets residue to zero per account, and the statements oracle
    asserts that independently.
- **C-4 Presentation** by natural balance via `ref_cache.ledger_class_is_debit_normal` (its first
  runtime consumer): debit-normal (Asset/Expense) as-is; credit-normal (Liability/Income/Equity)
  negated. `Decimal` only. **Non-loan liability sign rule (stated, not silently normalized):** the
  statements present the ledger faithfully. A non-loan liability whose anchors follow the
  owed-as-negative convention renders as a positive Liabilities line; a positively-anchored one
  would render negative -- unlike `net_worth_kernel.sum_net_worth_at_period`, which
  `-abs()`-normalizes both conventions. The statements oracle pins a negatively-anchored liability
  case; the prod-clone verification checks real data for positively-anchored non-loan liabilities
  and, if any exist, the developer decides data cleanup versus a documented presentation note. No
  `-abs` band-aid.
- **C-5 Retained earnings is derived, never posted** (no closing entries, ever): the Equity
  section carries a computed line equal to the negated cumulative Income+Expense net through the
  as-of date.

Storage stays UTC: `journal_entries.entry_date` keeps the `_civil_settle_date` UTC rule; the
display-timezone conversion happens only in readers, via `app.utils.dates.to_display_civil_date`
(which composes the existing `to_display_date`).

Known, accepted divergences (documented, oracle-pinned): an early-settled source appears in its
(future) pay-period window under C-2 and in its actual paid year under C-3 -- both honest answers
to different questions; the balance sheet is the POSTED ledger's statement and excludes modeled
growth between true-ups (Net Worth's job, via the seam).

---

## 3. Write side: anchor-equity postings for all non-loan accounts

New package `app/services/account_posting_service/` mirroring `loan_posting_service`
(`_walk.py`, `_anchors.py`, `_sync.py`, `__init__.py`); journal entries written only through
`posting_service._emit_balanced_entry`; ledger rows only via the new
`ledger_account_service.get_or_create_anchor_equity_account(user_id, account_id)`.

### 3.1 Chart shape: per-account `anchor_equity` kind (index re-key)

New `LedgerAccountKindEnum.ANCHOR_EQUITY` row shape: `account_id` set, Equity class, snapshot name
`"<account.name> -- Opening"`. Compatible with every existing CHECK (verified;
`ck_ledger_accounts_loan_shape` is `loan_account_id IS NULL OR ...`). The partial unique
`uq_ledger_accounts_account` re-keys to `(account_id, kind_id)` so the linked row and its equity
twin coexist. Rejected: reusing `loan_account_id` (every name/guard says "loan"); one equity
account per user (byte-identical to the deliberately-non-unique orphan shape, racy, loses the
loan-parallel per-account presentation).

Load-bearing hardening that must land BEFORE any writer: `posting_reads._ledger_account_for`
(`.one_or_none()` raises `MultipleResultsFound` the moment a twin exists) and
`ledger_account_service.create_ledger_account_for_account`'s idempotency lookup both gain a
LINKED-kind filter. `archive_helpers.account_has_ledger_postings` deliberately stays kind-agnostic
(correct for Guard 5 -- the twin's legs appear and disappear pairwise with the linked row's).

### 3.2 The walk (MOMENT-granular)

Engine semantics (verified): settled items are excluded from every period's sum -- the anchor is a
moment-of-assertion fact that already reflects all settled activity known at that moment
(`balance_calculator.py:13-14, 103-107`; `apply_anchor_true_up` docstring: "the user is declaring
'my real checking is now $X' -- every past-dated debit purchase is already in that number"). A
period-granular walk would mis-state the balance sheet by every pre-true-up settle in the anchor
period (the pre-approval review's CRITICAL-1). Per (account, scenario):

- **Anchor facts**: all `AccountAnchorHistory` rows ordered by `(created_at, id)` -- matching
  `resolve_anchor`'s max-`created_at` pick. First row = OPENING, rest = TRUE-UPs. Assertion
  instant = the row's `created_at` (UTC).
- **Source facts, read back from the ledger** (never re-derived from transaction rows --
  timing-proof against reverse-before-delete, future-proof for new source kinds): group the
  account's linked-ledger postings, excluding `account_opening`/`account_trueup` sources, by
  SOURCE; each source contributes its CURRENT net and its CURRENT attribution instant = the
  source's `paid_at` (transfers: the income shadow's), falling back to its pay period
  `start_date` at midnight UTC when NULL. A reverted source nets to zero and drops out regardless
  of partition.
- **Per anchor, in order**: `ledger_before = prior correction deltas + SUM(net of sources whose
  attribution instant <= this assertion instant)`; `delta = anchor_balance - ledger_before`
  (ledger-native sign -- holds for Asset AND Liability non-loan accounts; the engine never
  branches on class); legs `{linked: +delta, equity: -delta}`, posting kinds `opening`/`trueup`
  (REUSED -- the journal SOURCE distinguishes account from loan corrections).
- **Why this is correct**: at the true-up chokepoint the fresh delta equals
  `asserted - current live linked total` (every settled source's instant precedes "now"), so the
  go-forward wiring is a plain reconcile-to-target and the pure walk reproduces it after the fact
  (`created_at` and the ledger are immutable). Pre-anchor settles in ANY period are absorbed by
  the opening/true-up deltas; post-assertion settles ride on top. A source reverted after a
  true-up self-heals to the engine's answer. Zero-delta corrections book nothing (a fresh $0
  account mints no entries and no equity row, staying hard-deletable); a stale posted correction
  reverses via its empty-target key.
- **`entry_date` = the anchor's UTC civil date** (the `AnchorPoint.as_of_date` convention and the
  loan-correction precedent). `pay_period_id` = the history row's period (NOT NULL FK; satisfies
  R2 -- the period of what it corrects). Reconcile key `(source_kind_id, entry_date)`; two
  same-day same-kind anchors merge to one target landing on the later value (mirrors the F-103
  unique-index semantics and the loan merge behavior). The late-evening-ET/UTC-day edge on
  `entry_date` is identical to shipped loan corrections; documented.
- Delta emission, posted-leg read-back keyed by `(source_kind_id, entry_date)`, and
  flush-never-commit mirror `loan_posting_service/_anchors.py`. Shared primitives `delta_legs` /
  `summed_posting_legs` move from `loan_posting_service/_common.py` to a new
  `app/services/_posting_reconcile.py`; the loan package re-points.

### 3.3 Lifecycle wiring (exhaustive anchor-writer inventory)

1. `account_service.create_account` (after the ledger pairing): sync all scenarios; loud log +
   skip when no baseline scenario exists (test fixtures only; production users get a baseline at
   registration).
2. `anchor_service.apply_anchor_true_up`: inside the existing try, after
   `clear_entries_for_anchor_true_up` (verified side-effect-free for posted amounts: it flips
   `is_cleared` on PROJECTED parents only and the Step-3 effect formula never reads `is_cleared`),
   before `commit()`.
3. `routes/accounts/crud.py update_account` (the direct history write): extend the guarded
   callable that already runs on `anchor_changed`.
4. `pay_period_admin.reset_pay_periods`: `resync_user_account_anchor_postings(user_id)`
   immediately after the R7 loan resync, same transaction. Post-reset is clean by construction
   (the wipe CASCADEd old entries + history; `_reanchor_accounts` staged one fresh row per
   account; the zero-settled gate guarantees no effects).
5. Effect-time self-heal INSIDE `posting_service` at the tails of `sync_transfer_postings` /
   `sync_transaction_postings` (covers `reverse_postings_before_delete`): when deltas were emitted
   for a non-loan account, resync that (account, scenario) iff `min(source's current attribution
   instant, source's pay-period start-of-day) <= the account's latest anchor instant` -- the
   period-start arm catches revert/move cases whose OLD attribution preceded the anchor.
   Deliberately conservative, cheap (one indexed latest-anchor lookup), oracle-pinned.
   Function-local import with the standard rationale (reverse dependency).
6. `routes/grid.py create_baseline` (the recovery path for baseline-less users): resync the
   user's accounts after minting the baseline so openings are not silently stranded.
7. **Type-change guard**: `account_validation._validate_update_account` refuses an
   `account_type_id` change that crosses the AMORTIZING boundary or flips the linked ledger's
   Asset/Liability class while the account has ledger postings (the Guard-5 pattern). Without it a
   re-typed account strands one correction family and double-counts under the other. Pre-existing
   latent gap; Step 5 makes it visible money, so Step 5 closes it. **Second crossing vector (C4
   adversarial review M2): `routes/accounts/types.py update_account_type` lets an owner flip
   `has_amortization` on a CUSTOM type in place -- crossing the boundary with no `account_type_id`
   change. The same C6 guard must also refuse a `has_amortization` change while the owner has
   accounts of that type carrying ledger postings.**

Scenario enumeration for account-global events: `{baseline} UNION {scenario_ids of entries with a
posting on the account's linked ledger}` -- the cash analog of the loan rule; R8 owns the residual
multi-scenario policy.

### 3.4 Migrations (three) + deploy hook

1. **Ref rows**: INSERT `ref.posting_sources` `account_opening` / `account_trueup` +
   `ref.ledger_account_kinds` `anchor_equity` (no new posting kinds -- `opening`/`trueup` are
   reused). Enum members + `ref_seeds.py` dual-seed + parity test. Downgrade deletes the rows
   (safe: the boundary migration downgrades first; RESTRICT FKs enforce the order).
2. **Index re-key** (destructive; developer-approved via this plan; `Review:` docstring line):
   drop `uq_ledger_accounts_account`, create `uq_ledger_accounts_account_kind (account_id,
   kind_id) WHERE account_id IS NOT NULL`; model `__table_args__` + taxonomy docstring updates
   (8 -> 9 kinds; the COALESCE display-rule caveat; the CASCADE-impossibility paragraph).
   Downgrade fails loud (RuntimeError with the diagnostic SELECT) if any `anchor_equity` twin
   still exists -- unreachable via the linear chain, defense against out-of-band runs.
3. **Data boundary** (mirror `f3d6b1a8c2e4`): upgrade = documented no-op (forward population is
   the go-forward wiring + deploy hook; the walk needs `ref_cache`/services and the migration
   host runs `create_app(init_ref_cache=False)`). Downgrade deletes `account_opening` /
   `account_trueup` entries (legs cascade) then `anchor_equity` ledger rows, resolving ref ids by
   name, fail-loud.

Deploy hook: extend `scripts/init_database.py` with `backfill_all_account_anchor_postings()`
after the loan backfill -- backfill == go-forward by construction (same sync code); idempotent,
including on a database where go-forward corrections already exist; existing-database path only.

### 3.5 Accepted behavior changes

- Periods holding a non-zero opening/true-up flip lock reason `ACCOUNT_ANCHOR` ->
  `LEDGER_POSTINGS` (the gate keys on `pay_period_id` and precedes `ACCOUNT_ANCHOR`); UI badge
  text shifts accordingly.
- Any account with a non-zero anchor becomes archive-only (Guard 5) the moment its opening posts
  -- identical to shipped loan behavior; $0-anchor accounts stay hard-deletable.
- Rolling edge: an all-future schedule anchors to a future period; its correction can
  `LEDGER_POSTINGS`-lock that period, and a future-dated opening is excluded from an as-of-today
  balance sheet (whole-entry exclusion keeps the tie-out green). Rare; oracle-pinned.

---

## 4. Read side: statements, routes, enforcement

### 4.1 `app/services/ledger_report_service/` (package)

- `_types.py`: frozen dataclasses `StatementWindow`, `StatementLine`, `TrialBalanceTieOut`,
  `IncomeStatementReport`, `BalanceSheetReport`. Decimal-only.
- `_attribution.py`: the shared core BOTH statements consume (shared code is what makes
  articulation automatic): `dated_account_nets(user_id, scenario_id)` -> per-(ledger account,
  attribution date) nets from three batched queries -- transaction-linked (join `Transaction` +
  `PayPeriod`), transfer-linked (two-step C10 shape: grouped nets, then one batched income-shadow
  date load), sourceless (grouped by `entry_date`). No per-account loops.
- `_income_statement.py`: `compute_income_statement(user_id, window)`. Pay-period window = one
  grouped query on `pay_period_id` (uses `idx_journal_entries_user_scenario_period`); calendar
  windows filter the attribution core. Lines = Income/Expense-class ledger accounts only
  (category rows, the Uncategorized fallback, orphans, per-loan interest/escrow rows), sorted by
  label.
- `_balance_sheet.py`: `compute_balance_sheet(user_id, as_of)`. Fold nets dated <= as_of;
  sections by class; `retained_earnings` derived (C-5); tie-out = assets vs
  liabilities-plus-equity AND the mechanical ledger-net == 0; `in_balance` requires both.
- Display names: one chart load with `joinedload(category)`; LIVE `category.display_name` for
  category rows (renames reflected); the snapshot `LedgerAccount.name` for orphan / fallback /
  per-loan / equity rows; branch on `kind_id` via ref_cache, never NULL patterns.
- `get_baseline_scenario` first; `None` -> empty report with a green tie-out.
- Template footnote + this doc record the honesty boundary: this is the POSTED ledger's
  statement; modeled growth between true-ups is Net Worth's job (the seam).
- W9906 posture: the report functions are statement aggregates, not balance-at-T producers; that
  exclusion rationale is recorded in the checker header. W9908 fences the models; the statements
  oracle fences the numbers.

### 4.2 Routes / templates / CSV

- `app/routes/analytics.py`: `income_statement_tab()` (params
  `window`/`period_id`/`month`/`year`/`format`; IDOR-check `period_id` via
  `_validate_owned_or_abort`; generalize `_resolve_variance_params` -> `_resolve_window_params`
  shared with the variance tab) and `balance_sheet_tab()` (param `as_of` ISO date,
  parse-or-today, clamped to [2000-01-01, today]); CSV branch before the HTMX guard; non-HTMX
  redirects to `analytics.page`.
- `analytics/analytics.html`: two new pills (same hx-get / hx-target / hx-indicator pattern). No
  new JS; CSP holds by construction.
- `analytics/_income_statement.html` / `_balance_sheet.html`: money macro, card + table
  `text-end font-mono`, window toggle cloned from `_variance.html`, a date input wired like the
  year select, the tie-out footer card with a success/danger badge, empty states.
- `csv_export_service.export_income_statement_csv` / `export_balance_sheet_csv` following
  `export_year_end_csv`; filenames per the variance convention.

### 4.3 The L9 switch (Schedule-A)

New pure helper `app.utils.dates.to_display_civil_date(paid_at, fallback)` composing
`to_display_date` (the display-timezone counterpart of `posting_service._civil_settle_date`,
which stays UTC as the storage rule). `confirmed_loan_interest_in_year` switches to it; docstring
updated. No existing test assertion moves (attribution tests use noon-UTC instants); NEW boundary
tests pin an 8:05pm-ET Dec-31 settle to the earlier year.

### 4.4 Enforcement

- **W9908 `shekel-ledger-model-bypass`** (new checker in the `tools/pylint` plugin): an IMPORT
  fence -- modules outside the allowlist may not import the ledger models by module path
  (`app.models.journal_entry`, `app.models.ledger_account`) or by name from `app.models`
  (`Posting`, `JournalEntry`, `LedgerAccount` -- the F-1 shape). Allowlist (grep-verified
  complete against every current importer + the new packages): `app.models`,
  `app.services.posting_service`, `app.services.posting_reads`,
  `app.services._posting_reconcile`, `app.services.loan_posting_service` (prefix),
  `app.services.account_posting_service` (prefix), `app.services.ledger_account_service`,
  `app.services.ledger_report_service` (prefix), `app.services.pay_period_admin`,
  `app.utils.archive_helpers`. Reuses the fail-closed `_module_in_allowlist`. W9906's
  reader-name list is NOT extended.
- Gate lockstep (the gate-consistency test forces every occurrence): CI `--fail-on` (both
  lines), `.pre-commit-config.yaml` (all three occurrences), `scripts/hooks/post-edit-python.sh`,
  `_CANONICAL_FAIL_ON` in `tools/pylint/tests/test_fail_on_gate_consistency.py`, the CLAUDE.md
  lists.
- **Test-side F-1 closure**: the loan oracle's two import-fence detectors also match
  `from app.models import Posting/JournalEntry/LedgerAccount`; the negative control extends to
  prove it.

---

## 5. Tests

- **Write-side oracle** `tests/test_integration/test_posting_ledger_account_anchor_reconciliation.py`:
  the ABSOLUTE invariant per non-loan account (`account_posting_total == latest anchor balance +
  SUM(current nets of sources attributed after the latest assertion instant)`, cross-checked
  against independently-summed transaction-table effects); ledger-through-each-assertion-instant
  == that asserted balance; pre-true-up settle absorbed / post-true-up settle rides on top (the
  CRITICAL-1 regression case); revert-after-true-up self-heal; pre-anchor absorption; same-day
  anchor merge; a NEGATIVELY-anchored liability account; zero-delta books nothing;
  scenario/owner isolation; backfill == go-forward; a non-vacuity injection.
- **Statements oracle** `tests/test_integration/test_posting_ledger_statements.py` (the fourth
  reconciliation oracle): hand-computed income statement + balance sheet on a rich fixture
  (categories, fallback, loan interest+escrow, transfers, an anchor true-up, a cross-year revert,
  the 8:05pm-ET Dec-31 settle, an early-settled future-period source, NULL-`paid_at` fallback,
  hard-delete residue nets-zero-and-dropped); `A = L + E` at >= 4 as-of dates including one
  inside an anchor period before and after the assertion date; articulation (income net +
  windowed equity corrections == equity delta between bounding sheets); period-vs-calendar
  agreement on an aligned fixture; transfers never touch the income statement; a
  liability-account line signs correctly; orphan/live-rename labels; scenario/owner isolation; a
  tie-out tamper injection (raw single leg -> red).
- **Unit/service tests**: walk + reconcile (hand-computed deltas incl. the moment partition), the
  anchor-equity resolver guards, the type-change guard, ref-seed parity, checker tests
  (`tools/pylint/tests/`), route tests (auth, HTMX partial, non-HTMX redirect, CSV
  content/filename, param clamps, IDOR 404).
- **Existing tests that legitimately change** (behavior change; the developer approved this plan
  as the confirmation), enumerated up front rather than discovered at the full-suite gate:
  per-account changes-only invariants -> the superseded absolute form in
  `test_posting_ledger_reconciliation.py` (:343-346, :441-444, :593-596) and
  `test_posting_ledger_cash_reconciliation.py` (:625-641, :949-952); `account_posting_total`
  assertions against seeded accounts in `test_transfer_posting_lifecycle.py`,
  `test_transaction_posting_lifecycle.py`, `test_posting_service.py`,
  `test_loan_posting_wiring.py`, `test_loan_posting_backfill.py`, `test_pay_period_truncate.py`;
  chart-shape assertions (`len(ledger_accounts_for_account) == 1` and siblings) in
  `test_ledger_account_backfill.py:168,188,206,232-233` and the other `test_ledger_account*.py`
  files; `tests/_test_helpers.create_account_of_type` ($100 sentinel) now mints a twin + opening
  everywhere; `seed_user` reorders (baseline Scenario BEFORE `create_account`, matching
  production -- the seeded Checking then carries its $1000 opening) and
  `_drop_seed_user_bootstrap` resyncs; pay-period lock-reason tests flip `ACCOUNT_ANCHOR` ->
  `LEDGER_POSTINGS` (`test_pay_period_admin.py:134-143`, `test_pay_period_truncate.py:340,:380`);
  reset tests gain one opening entry per account; hard-delete route tests on non-zero-anchor
  accounts now hit Guard 5.  **Kind-agnostic oracle HELPERS that silently self-cancel once twins
  exist** (from C3's adversarial review; each needs a LINKED-kind filter or a deliberate per-kind
  split with a comment): `test_posting_ledger_reconciliation.py` `_independent_ledger_sum` (:123)
  and `_legs_by_account` (:240) -- an anchor correction lands linked `+X` / twin `-X` on the same
  `account_id`, so a bare-account_id sum cancels the anchor legs and vacuously reproduces the
  changes-only figure; `test_posting_ledger_cash_reconciliation.py` :165 (same shape) and :1255
  (a `.scalar()` on bare account_id -- raises loudly instead);
  `tests/_test_helpers.ledger_accounts_for_account`'s "at most one" docstring.  The loan oracle's
  bare-account_id helper (:360) stays correct only because the account walk is non-loan-only --
  C4's resolver guard must enforce that so the assumption holds by construction.

---

## 6. Commit sequence

1. **C1 -- L9 helper + Schedule-A switch** (independent): `to_display_civil_date`; the
   `confirmed_loan_interest_in_year` switch; boundary tests; the L9 annotation in the review doc.
2. **C2 -- Ref rows**: enums + ref_seeds + inline-seed migration + parity tests.
3. **C3 -- Index re-key + lookup hardening**: the `(account_id, kind_id)` unique migration
   (fail-loud downgrade guard); model docstrings; the two LINKED-kind filter fixes;
   `test_ledger_account*.py` shape-test updates. Must precede any writer.
4. **C4 -- Anchor-equity chart resolver** + guards + tests.
5. **C5 -- Walk + reconcile (pure, unwired)**: `_posting_reconcile.py` extraction (loan package
   re-pointed); `account_posting_service/{_walk,_anchors,_sync}.py` + unit tests.
6. **C6 -- Lifecycle wiring + oracle supersession** (one commit -- the fixture reorder breaks the
   old invariants, so they move together): the seven wiring points incl. the type-change guard
   and `create_baseline`; the `seed_user` reorder + `_drop_seed_user_bootstrap` resync; the
   enumerated test-file updates from Section 5.
7. **C7 -- Backfill**: `backfill_all_account_anchor_postings` + the deploy-hook extension + the
   data-boundary migration; idempotence / no-double-post / downgrade-scope tests.  The boundary
   migration also closes the C6-to-C7 transitional downgrade hazard the C6 review flagged (L1):
   with corrections posted go-forward but no boundary migration beneath them, a downgrade below
   the ref-source migration `a4c8e2f6b1d3` on a populated DB fails RESTRICT on the
   `account_opening`/`account_trueup` rows -- loud, not silent, but open until C7 lands.  C7 also
   makes true the promise the loan boundary test's kind sweep now relies on
   (`test_loan_posting_backfill.py`: the account-correction sources are excluded because "their
   OWN boundary migration downgrades first in the linear chain").
8. **C8 -- Write-side oracle.**
9. **C9 -- Reporting service** + service tests.
10. **C10 -- Routes + templates + tabs** + route tests.
11. **C11 -- CSV export** + tests.
12. **C12 -- Enforcement**: W9908 + gate lockstep wiring + checker tests; the test-side F-1
    detector fix; the F-1 annotation.
13. **C13 -- Statements oracle + docs close-out**: `test_posting_ledger_statements.py`; annotate
    M3 (reader half) / R9 / the header note in the review doc; update
    `level1_level2_scope_and_fitness.md` Status. Final gate: full suite alone + `pylint app/
    scripts/` 10.00, output shown.

### Follow-up commits (from the per-commit adversarial reviews)

Root-cause work the reviews surfaced that is real but NOT money-wrong today -- every current
caller was verified safe, so none of these gates Step 5's PR.  Recorded as commits so they are
scheduled work, not lore.  F1 and F3 land on this branch after C13 (or as the first commits of
the next arc); F2 is a requirement pinned to R8's owner.

- **F1 -- Settled-transfer attribution-mutation seam (C6 review M2). DONE**
  (after C13, own commit). As built exactly as specified below: `pay_period_id`
  added to `_POSTING_RELEVANT_FIELDS` (a settled period move now fires the Step-2
  reconcile, which re-posts R2-correctly per-(account, period) and self-heals);
  a settled `paid_at` edit resyncs the two endpoint accounts' anchor corrections
  directly (`sync_account_anchor_postings` per endpoint), since a `paid_at` move
  changes no leg so the reconcile-to-target and its tail self-heal cannot see it.
  The reconcile tail was extracted to a `_reconcile_postings_after_update` helper
  to keep `update_transfer` within its branch/statement budget (the added
  branches tripped `too-many-branches`/`too-many-statements`); the constant's
  invariant note was rewritten (`pay_period_id` now in the set, `paid_at`
  handled by the direct resync). `account_posting_service` imported at top level
  (no cycle: it never reaches `transfer_service`; `posting_service`'s reverse
  import of it is function-local). Two oracle cases in
  `test_posting_ledger_account_anchor_reconciliation.py`
  (`TestSettledTransferAttributionMutation`): a settled period move (asserts the
  cash nets to zero in the old period and +150 in the new -- R2 -- with the
  account total unchanged and the absolute invariant holding, no manual sync)
  and a settled `paid_at` move across the anchor (asserts both endpoints
  re-absorb the transfer into their openings and read their anchors, invariant
  holding). Both mutation-proven non-vacuous (dropping `pay_period_id` fails the
  period case; neutering the `paid_at` resync fails the paid_at case). Original
  spec follows.

  `transfer_service.update_transfer` accepts `pay_period_id` and `paid_at` on a SETTLED transfer
  with no ledger reconcile (`_POSTING_RELEVANT_FIELDS` omits both; the invariant note now sits on
  that constant), so a future service caller could move the walk's attribution with no delta
  entries and no effect-time self-heal -- a silently stale anchor correction.  Today the routes
  make this unreachable (the finalised lock refuses settled-row period edits; the shadow PATCH
  drops `pay_period_id` and passes `paid_at` only with a status change; carry-forward moves
  Projected rows only).  The commit: add `pay_period_id` to `_POSTING_RELEVANT_FIELDS` (the
  reconcile is idempotent, and a settled-row period move then reconciles R2-correctly AND fires
  the tail self-heal), and for `paid_at` -- which changes attribution WITHOUT changing any leg,
  so the tail cannot see it -- resync the two endpoint accounts' corrections directly
  (`sync_account_anchor_postings` per (account, scenario)) when a settled transfer's `paid_at`
  kwarg is applied.  Rejected: refusing settled-row mutation at the service tier (would hard-code
  today's route policy into the service and still leave the seam untested); a band-aid reader
  workaround.  Tests: a service-level settled period-move and a settled `paid_at`-only edit, each
  asserting the absolute invariant holds with no manual sync.

- **F2 -- Scenario-creation correction sync (C6 review M1; lands WITH R8 / scenario clone, not
  before).**  A non-baseline scenario receives an account's opening only via the effect-time
  self-heal, which fires only when the scenario's first settle lands at-or-before the latest
  assertion instant (in practice: same UTC day) -- otherwise the scenario's ledger reads
  changes-only until the next account-global sync.  Unreachable today (the only
  scenario-creation surfaces are registration and `create_baseline`, both baseline-only; both
  statement tabs read the baseline only).  The requirement, pinned here so R8 cannot miss it:
  any path that creates or clones a non-baseline scenario must run
  `sync_account_anchor_postings(account_id, new_scenario_id)` for each of the owner's non-loan
  accounts in the same transaction.  The two oracle sweeps
  (`test_posting_ledger_reconciliation.py` / `..._cash_...`) document the same-day caveat and
  must have their scenario caveats retired by that commit.

- **F3 -- Payment-timeliness display-timezone pass (C1 review, out of scope for L9). DONE**
  (after C13, own commit). `Transaction.days_paid_before_due` now truncates
  `paid_at` via `app.utils.dates.to_display_date` (the display-timezone civil
  day); `year_end_summary_service/_spending.py` was DRY-refactored to route its
  paid_on_time / avg-days loop through that same model property (it had a
  duplicate inline `paid_at.date()` calc), so the fix lives in one place and its
  third consumer, `spending_trend_service._compute_avg_days_before_due`, inherits
  it. Boundary tests at both the model
  (`TestDaysPaidBeforeDue.test_days_paid_before_due_evening_eastern_is_on_time`)
  and the year-end service (`TestPaymentTimeliness.
  test_payment_timeliness_evening_eastern_is_on_time`): an 8:05pm-Eastern settle
  on the due date is on time (0 days), not a day late by UTC drift; both pass
  under `TZ=UTC`. Three pre-existing timeliness fixtures that used midnight-UTC
  instants (ambiguous: the prior Eastern day) were re-pinned to noon UTC so their
  documented intent holds under the display-tz rule, passing with their original
  expected values. Original spec follows.

  `_spending.py:306` and `Transaction.days_paid_before_due` still truncate `paid_at` in UTC --
  the same wall-clock class L9 fixed for Schedule-A, but statistics rather than tax-year money.
  The commit: route both through `app.utils.dates.to_display_civil_date` with boundary tests
  (an 8:05pm-ET settle on the due date must not count as paid late/early by UTC drift).

## 7. Verification (end-to-end)

1. Per-commit: `SKIP_DB_RESTART=1 ./scripts/test.sh <targeted files> -v`; final
   `./scripts/test.sh` (full suite, run alone) with pass counts shown.
2. Migration round-trip on the rebuilt template: `flask db upgrade` / `downgrade` x2, then
   `python scripts/build_test_template.py`.
3. **Prod-clone verification** (developer-gated): clone prod -> dev DB, run migrations + the
   deploy hook, then assert by hand: trial balance still 0; every non-loan account's
   `account_posting_total == latest anchor + post-assertion settled effects`; the balance sheet
   tie-out green on real data; NO positively-anchored non-loan liabilities exist (else the C-4
   decision point returns to the developer); spot-check the income statement against the
   year-end spending totals for a known year.
4. Live-drive the dev container: open `/analytics`, exercise both tabs (window toggles, as-of
   date, CSV downloads); settle a transaction then true-up the anchor and watch the balance
   sheet land exactly on the asserted balance (the CRITICAL-1 scenario); both themes.
5. PR `feat/actuals-reporting` -> `dev` after developer review; prod ship follows the
   established dev -> main pipeline separately.
