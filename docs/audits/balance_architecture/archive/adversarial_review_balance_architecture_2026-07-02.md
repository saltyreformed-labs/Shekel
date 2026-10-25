> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Adversarial review: the balance architecture (Option D), end to end

**Date:** 2026-07-02. **Reviewer:** independent adversarial pass, commissioned as a complete
re-evaluation with no anchoring on past decisions.

> **Remediation status (2026-07-02, branch `fix/ledger-period-attribution`):** R1 and R2 are
> IMPLEMENTED. H2 is fixed (`8ad3a81`: the split walk posts at settlement; the tax hybrid excludes
> settled due slots; verified live on the real Mortgage -- the early-settled period now drops by
> the real 276.72 principal, not the 1,910.95 raw cash). H1 and the M3 storage-level class are
> fixed (`2ffefa0`: per-(account, period) reconcile in `posting_service` and the loan payment
> reconcile; reversals carry the period and latest date of what they reverse; the
> `LEDGER_POSTINGS` truncate/regenerate gate refuses periods whose entries do not net to zero per
> ledger account, proven non-vacuous by mutation). H3 is fixed (`ad24d84`, R3: W9907 now fences
> all four statically visible `status_id` write forms with a born-Projected constructor rule, and
> W9906 gained the import-level fence closing the aliased-import evasion). M1 is fixed (R4: the
> ledger tables are append-only at the DB tier for `shekel_app` -- migration `e3c23fadb21d` plus
> the every-start re-assert in `init_db_role.sql`; cascades verified live on dev first). M5 and
> M4(a) are fixed (R5: the +$10 walk injection, the tamper proofs driven through the real sweep
> helpers under `pytest.raises`, the resolver-stack ledger-free fence, and the non-empty sweep
> asserts are all now executable in CI, across the three reconciliation oracles). M2 is fixed
> (R7, `654c991`: `reset_pay_periods` re-syncs the reset user's loan genesis postings after the
> wipe, in the same transaction, via a new PER-USER `resync_user_loan_postings` scoped by
> `load_loan_account_ids_for_user` -- not the deploy-wide backfill -- so a configured-loan user's
> loan reads no longer degrade to the replay fallback after a reset; the overclaiming "no posted
> period is ever wiped" comment is corrected). M7 and M4(b) are fixed (R6,
> `5c64107`/`814b407`: the ARM-step oracle case pins a HAND-COMPUTED post-step ledger literal --
> the shared-`rate_period_engine` teeth that a lockstep bug cannot hide from -- and the
> biweekly-collision case pins the reader/resolver attribution split; a transfer OUT of a loan is
> now FORBIDDEN at the `create_transfer` chokepoint, so the income-only-shadows invariant the
> oracle relied on is enforced, not assumed). The independent adversarial review of the R6 commits
> raised four items, all now addressed: G-1 (the biweekly test pinned only display dates, not the
> LEDGER per-period buckets) is closed by `8d4fcb7`; T-1 (the new guard 500ed on the
> recurring-template route path) by `703af6a`; D-3 (biweekly wording) by the accurate M7/R6
> annotations; E-1 (the escrow-not-a-lockstep claim was unverified) by the code trace now cited in
> the M4 annotation. Full suite 6874 passed at HEAD (R5's own figure was
> 6865, not the 6861 first recorded here; R6 and its G-1/T-1 follow-ups, R7, and the earlier
> follow-up review's M-1/M-2/M-3/L-1 fixes are green); pylint holds 10.00 with every `--fail-on`
> checker (`transfer_service.py` is now at exactly its 1000-line cap after the R6 guard wiring -- a
> further module split is warranted, tracked in R10 -- now DONE, see below). M3's reader-contract
> half (Step-5 reporting rules), R8, and R9 remain open; R10 housekeeping is IN PROGRESS
> (transfer_service split, L1, L8, L2, L10, the `shekel_checkers` package split with its
> `tools/pylint/` 10.00 floor, and L6's cross-page route-render lock all DONE 2026-07-03, with the
> L9 / C11 decisions recorded; only the L9 display-tz attribution follow-up remains, per-sub-item
> commits in the R10 note under the Section 6 table).
>
> **Step-5 build (2026-07-03, branch `feat/actuals-reporting`, actuals reporting):** the open items
> this review left for Step 5 are now closed on that branch (implementation plan
> `implementation_plan_actuals_reporting.md`, C1-C13). **M3's reader-contract half is DONE** --
> the ledger-wide attribution rule is recorded as the normative reader contract (that plan's
> Section 2: whole-source inclusion; pay-period windows by `pay_period_id`; calendar / as-of windows
> attribute each source's net to its CURRENT paid date in the DISPLAY timezone, sourceless
> corrections by `entry_date`, hard-delete residue dropped whole; natural-balance presentation;
> derived retained earnings) and IMPLEMENTED once in `app.services.ledger_report_service._attribution`
> so both statements articulate. **R9 is DONE** -- every non-loan account now posts anchor-equity
> opening / true-up corrections (write side, C2-C8 + the C7 deploy-hook backfill), so the trial
> balance closes app-wide, and an income statement + balance sheet with a two-part tie-out read that
> closed ledger (C9-C11); the L9 Schedule-A switch (C1) and the W9908 ledger-model import fence (C12)
> landed with it. Reads for the app's balance screens INTENTIONALLY stay on the `balance_at` seam (no
> cash read switch -- the Step-5 non-goal). The reader contract is oracle-pinned by the fourth
> reconciliation oracle, `test_posting_ledger_statements.py` (C13), which cross-checks both
> statements against an independent by-class re-derivation and asserts `A = L + E`, articulation, and
> period-vs-calendar agreement, mutation-proven non-vacuous. R8 (multi-scenario) remains the only
> open item from this review (reports read the baseline scenario only, by design).
**Scope:** everything in `docs/audits/balance_architecture/` (all 11 documents read in full) and
the code that implements it: the Level-1 `balance_at` seam, the posting ledger (Steps 2-4), the
temporal-escrow prerequisite, the loan read switch (PR #52, at prod HEAD `2d81705`), the fence
checkers, and the four reconciliation oracles.
**Method:** four independent audit passes (fence checkers; ledger writer/CASCADE surface; oracle
test quality; read-switch reader trace), each claim re-verified against live code on `dev`, plus
seven live experiments against the dev database (all mutations rolled back; final state verified
identical). Nothing below is asserted without a citation or a demonstration.

---

## 1. Verdict

**The architecture is sound, the direction is right, and the implementation quality is far above
what a solo project normally achieves. The money amounts are correct -- verified live on real
data. The residual weakness is concentrated in one theme: WHEN money is attributed (pay period
and date), not HOW MUCH.** Two real, reachable correctness defects were found (H1, H2), both in
period attribution, both invisible to every existing oracle because the oracles reconcile
scenario-wide sums and are period-blind by construction. A third finding (H3) is not a defect but
the largest un-mechanized hole in the enforcement story the project itself set as its standard.

No finding here invalidates a shipped design decision. Re-examined from scratch (Section 6),
Option D, the genesis read switch, and the staging discipline all hold up as the correct calls --
not merely defensible ones.

The single most important takeaway: **the ledger's amount invariants are machine-enforced three
layers deep (service check, deferred DB trigger, oracles), but its period/date attribution is
enforced zero layers deep.** Every defect found lives in that gap. Fixing the two HIGHs and
adopting one systemic rule for how correction entries are dated (Recommendation R2) closes the
gap before Step 5 (actuals reporting) would inherit it.

---

## 2. What was independently verified as sound

These are not restatements of the project's own claims. Each was re-derived or re-executed during
this review.

### 2.1 Live experiments (dev database, 2026-07-02, all rolled back)

1. **The headline off-schedule claim works end to end on real data.** Settling the real
   Mortgage's next payment with a +$500 actual (cash 2,410.95) through the real chokepoint
   (`transfer_service.update_transfer`): the ledger reader dropped by exactly 776.72
   (= 2,410.95 cash - 1,017.24 interest accrued on the REAL running balance 177,554.69 - 616.99
   escrow); the production read path agreed (176,777.97); the un-seeded replay still showed the
   contractual 177,277.97 -- exactly $500 apart; Checking moved by the full -2,410.95; reverting
   restored every number to the penny. The extra principal was captured with no true-up. This is
   the outcome the entire arc was built for, and it works.
2. **True-up posts a correction, not an edit.** `anchor_service.apply_loan_anchor_true_up` to
   175,000.00: reader and production path both jumped to exactly 175,000.00; the correction
   entry's legs were +2,554.69 on the Mortgage linked ledger and -2,554.69 on the per-loan equity
   account -- balanced, append-only.
3. **The deferred balance trigger works as claimed.** A raw-SQL single-leg entry was rejected at
   COMMIT ("has 1 posting(s); >= 2 required"); a raw-SQL unbalancing UPDATE was rejected at COMMIT
   ("postings sum to 10.00; must be 0").
4. **The documented no-DELETE gap is real** (see M1): a raw-SQL DELETE of one leg of a balanced
   entry succeeded silently; the trial balance broke (+500.00) with nothing at the DB tier
   catching it. (Rolled back.)
5. **Production-wide reconciliation holds on real data.** Per-account sweep across all 9
   accounts: every cash account's ledger net equals its settled-source effect to the penny (diff
   0.00), and each loan's difference equals exactly its per-loan corrections (Mortgage
   -183,287.54 = -(equity 177,483.43 + interest 3,336.15 + escrow 2,467.96)). Trial balance 0.00;
   zero unbalanced entries; 118 entries / 241 postings.
6. **Reader/resolver/production three-way parity on-schedule:** all three produced 177,554.69 for
   the Mortgage.
7. **Finding H2 demonstrated live** (Section 3).

### 2.2 Design and process strengths confirmed by code trace

- **The root-cause diagnosis was accurate.** The six balance producers and their four disagreeing
  boundary rules (`recurring_loan_balance_root_cause.md` Section 2c) were re-verified by the
  fitness doc and again during Level-1 planning; the fix (one seam owning all four per-kind rules,
  `app/services/balance_at.py:1-77`) matches the diagnosis exactly.
- **Option D's fact-versus-derivation principle is applied consistently.** Confirmed facts are
  posted once, append-only; projections stay live-computed; the one place the codebase had already
  learned this lesson (`income_service.live_projected_net`, the demoted
  `LoanParams.current_principal`) is extended, not contradicted.
- **Reconcile-to-target is genuinely idempotent and self-healing.** The delta design
  (`posting_service.py:571-784`) covers settle / revert / edit / cancel / delete / restore through
  one code path, reads the posted side back from the ledger (so a revert-and-recategorize reverses
  the OLD category -- the Step-3 CRITICAL fix, `posting_service.py:487-520`), and no-ops on
  repeats. Verified in experiments 1 and 2.
- **The sole-writer claim holds.** Every `JournalEntry` / `Posting` construction in `app/` funnels
  through `posting_service._emit_balanced_entry` (`posting_service.py:342-395`;
  `loan_posting_service/_anchors.py:300`, `_payments.py:242`); `LedgerAccount` is written only by
  `ledger_account_service` (`:180, :367, :591`). No bulk ops, no raw SQL outside migrations. FK
  actions match model-to-migration in every case.
- **The genesis walk's pre-anchor algebra is an identity, not a cleanup.** The walk resets the
  running balance at every anchor (`loan_posting_service/_walk.py:352-370`), so a pre-true-up
  payment's posted principal cancels arithmetically against the true-up correction's
  `owed_before`. The $3,821.90 naive-read-switch mis-statement class cannot regress via data
  drift, only via a code change to the reset logic, which
  `test_pre_anchor_payment_is_correctly_summed_under_genesis` pins.
- **The adversarial-review-between-steps process earns its cost.** The v1 Step-4 plan shipped a
  genuine cash-corruption CRITICAL (the `transfer_id` linkage that Step 2's `_posted_net` would
  have double-read); the review caught it pre-implementation and the `transaction_id` linkage
  dissolved it structurally (`implementation_plan_posting_ledger_loan_payments.md` Section 2.8,
  5.2). The Step-3 revert-and-recategorize CRITICAL and the H1 `is_fallback` collision were caught
  the same way. This process is a load-bearing part of the quality story and should continue.
- **Oracle discipline is real, not theater.** Hand-computed literals, independent SQL
  cross-checks, and executable negative controls (the cross-page seam-injection locks) are all
  present; the loan oracle correctly re-pointed its "independent producer" at the un-seeded
  resolver when the read switch would have made it a tautology
  (`test_posting_ledger_loan_reconciliation.py:461-493`). Gaps exist (M4, M5, M8) but the
  foundation is honest.
- **Deletion/archival of a loan is guarded twice:** `ledger_accounts.loan_account_id` is
  ON DELETE RESTRICT at the DB tier, and route Guard 5 archives any account whose ledger carries
  postings (`routes/accounts/crud.py:631-636`).

### 2.3 Scale (for the maintainability judgment)

The arc's production code is ~6,900 lines (seam + posting services + models + infrastructure),
its dedicated tests ~13,800 lines, plus 1,946 lines of checker infrastructure -- a 2:1
test-to-code ratio on the money core, inside a suite of 6,827 tests.

---

## 3. Findings

Ranked by severity. "Demonstrated" = reproduced live during this review. "Reachable" = a concrete
user flow exists today. "Latent" = requires a future feature or a raw-SQL operator action.

### H1 (HIGH, reachable): revert-and-move plus period truncate can strand half a reversal pair -- a permanent, unhealable ledger skew

> **FIXED 2026-07-02** (`2ffefa0`, R2): all three reconciles (transfer, transaction, loan split)
> now reconcile per (ledger account, pay period), so the reversal lands in period P -- the pair
> nets P to zero and F never holds a stray half.  Defense-in-depth: the truncate/regenerate
> classifier gained a `LEDGER_POSTINGS` hard lock for any period whose entries do not net to zero
> per ledger account (mutation-proven).  Regression locks: the route-level revert-and-move PATCH
> test (`test_posting_ledger_cash_reconciliation.py::TestRevertAndMoveReconciles`), the unit
> attribution locks in `test_posting_service.py::TestPeriodAttribution`, the loan twin in
> `test_loan_posting_service.py`, and the truncate gate pair in `test_pay_period_truncate.py`.
> The walkthrough below describes the pre-fix code (its line numbers predate the fix).

Plain-language walkthrough. You mark a $100 expense Paid in period P; the ledger books entry E1
(+/-100, stamped with period P). Later you realize it belongs in a future period F, so you do the
supported "revert and correct" edit: one PATCH that sets the status back to Projected AND moves
the row to period F. The finalised-edit lock deliberately lifts on a same-request revert
(`app/routes/transactions/mutations.py:63-66`, comment: "a settled row's period / due-date cannot
move anyway... unless the same PATCH reverts to Projected"). The handler applies the new
`pay_period_id` first, then reconciles last, so the reversal entry E2 is stamped with the NEW
period F (`posting_service.py:770`, `pay_period_id=txn.pay_period_id`). The net-zero pair now
straddles two periods: E1 in P, E2 in F.

Now truncate or regenerate the not-yet-started tail containing F (a supported settings action).
The lock classifier checks only settled TRANSACTIONS in the periods being deleted
(`pay_period_admin.py:793-800`, `_period_ids_with_settled_transaction`) -- it never looks at
journal entries. F holds only a Projected row, so the user sees the ordinary discard confirmation
and proceeds. The DB CASCADE then deletes F, the moved transaction row, and E2 -- but E1 survives
with `transaction_id` SET NULL.

Failure: the cash ledger account permanently carries +/-100 with no settled source row.
`account_posting_total != settled_transaction_effect` -- the reconciliation oracle's own
per-account invariant is broken, **and nothing can ever heal it**: the reconcile is keyed by
`transaction_id` (`posting_service.py:487-520`), which is now NULL. The mirror flow (settle in F,
revert-and-move back to P, truncate F) strands the original entry the same way.

Root cause: correction entries are dated/period-stamped at correction time, not at the period of
the postings they reverse (see M3, the systemic form of this). Recommendation: R2.

### H2 (HIGH, demonstrated): settling a payment in a not-yet-begun period posts its full cash with no split -- the loan balance reads ~$1,636 too low from period start until the next loan write

> **FIXED 2026-07-02** (`8ad3a81`, R1): the walk now splits EVERY settled payment
> (`_settled_income_shadows`, no period bound on the write side; the readers and the history
> reader keep the period-begun display bound via `_confirmed_shadows_through`).  Verified live on
> the same real Mortgage payment: the 2026-07-30 period now drops by the real 276.72 principal
> (1,910.95 cash - 1,017.24 interest - 616.99 escrow), not the raw cash, with today's balance
> untouched.  Companion fix: the year-end tax hybrid excludes projected schedule rows whose due
> slot a settled payment occupies (`loan_loaders.load_settled_payment_due_months`), so an
> early-settled payment's interest is counted exactly once -- and interest paid early across a
> year boundary deducts in the year PAID.  Regression locks:
> `test_early_settled_payment_splits_at_settle` (proven to fail on the pre-fix walk),
> `test_early_settled_payment_keeps_the_parallel_run_exact` (oracle, via the independent
> resolver), and `test_early_settled_payment_is_counted_exactly_once` (tax).  The walkthrough
> below describes the pre-fix code.

Plain-language walkthrough. On 2026-07-02 you mark the Mortgage payment for the period starting
2026-07-30 as Paid (nothing prevents settling a future-period transfer). Step 2's cash posting
fires immediately (it gates on settled only, `posting_service.py:571+`), booking the full
1,910.95 onto the loan's linked ledger, attributed to the July-30 period. But the Step-4 split
walk EXCLUDES payments whose pay period has not begun
(`loan_posting_service/_walk.py:259-269`, the `period_start <= as_of` eligibility bound), so no
interest/escrow correction posts.

Demonstrated live: after the settle, the confirmed reader at today was unchanged (correct -- the
period has not begun), but the per-period map showed the 2026-07-30 period dropping by the FULL
1,910.95 instead of the real ~275 principal. The moment that period begins, every ledger-fed
surface (loan card, savings tile, net worth, year-end) reads the balance 1,635.81 too low
(interest 1,018.82 + escrow 616.99), and the history rows show a phantom all-cash principal row.
It self-heals at the next loan write (next settle, true-up, rate edit, params edit) because the
sync is reconcile-to-target -- but there is no time-passage trigger, so the window is unbounded.

Root fix (recommended): split a settled payment regardless of whether its period has begun --
option (a) in the read-switch trace. The correction and cash legs share the entry's period, so
the reader's period bound remains the display gate and sums stay right in both windows. This also
restores the module's own stated invariant ("every Step-2 cash entry gets a matching
correction," `_walk.py:465-470`). Alternative (b), gating settle to begun periods, changes a user
affordance and is the lesser fix. Recommendation: R1.

### H3 (HIGH as enforcement gap; no defect today): born-Projected is a convention, not a machine rule

> **FIXED 2026-07-02** (`ad24d84`, R3): born-Projected is now a machine rule. W9907 matches all
> four statically visible `status_id` write forms outside the seam allowlist -- direct assignment
> (as before), the literal `setattr(x, "status_id", ...)` form, a `status_id` key or keyword in a
> bulk `.update()`/`.values()` call, and a `Transaction`/`Transfer` constructor `status_id=`
> kwarg whose value is not recognizably born-Projected (the ref-cache PROJECTED lookup or a
> `projected_id` name/attribute; any other value fails closed). The optional import-level fence
> also landed: W9906 flags a fenced producer imported BY NAME (aliased or not) outside its
> allowlist, closing the alias evasion of call-site matching. Zero in-tree violations by
> construction (every existing create site is genuinely born-Projected; `TransferSpec` and
> service calls are not model constructors). Regression locks: 32 new checker tests, every
> positive paired with its negative plus register-bound allowlist loops; non-vacuity proven
> end-to-end through `.pylintrc` (all five write forms and the aliased import fire; the
> recognized-Projected constructor, dynamic-`setattr` loop, and non-status bulk update stay
> silent). Statically invisible residuals stay with review and are documented on the checker:
> splatted `Transaction(**data)`, dynamic `setattr(txn, field, value)` loops, and a bulk payload
> dict built away from the call. The walkthrough below describes the pre-fix code.

The status fence (W9907) matches only `<expr>.status_id = ...` assignments
(`tools/pylint/shekel_checkers.py:843-895`). Constructor kwargs are documented out of scope,
"governed by the separate born-Projected create rule" -- but that rule is three conventions deep
(schemas omit `status_id`; routes overwrite with Projected; route tests), not a gate. The
enabling form ships in production today: `data["status_id"] = ...; Transaction(**data)`
(`app/routes/transactions/create.py:91-94, 139-144`) and `Transaction(status_id=...)`
(`recurrence_engine.py:184-189`). A future code path constructing a born-settled row would ship
with NULL `paid_at`, no `verify_transition`, and no ledger posting -- the exact failure mode the
Step-3 review called its highest risk -- and every gate stays green.

Two sibling forms are equally invisible and have enabling patterns in-tree for other columns:
`setattr(txn, "status_id", ...)` (a generic setattr loop over schema data exists at
`mutations.py:328-332`, currently guarded by a single `continue`), and bulk updates
(`db.session.query(Transaction).update({...})` is an established pattern at
`routes/templates.py:207, 543, 585, 674`; adding `"status_id"` to such a dict would bypass the
seam silently). Zero violations exist today; all three are closable with small AST extensions in
the existing checker style at zero migration cost. Recommendation: R3.

### M1 (MEDIUM, demonstrated; raw-SQL surface): the ledger's append-only property does not bind at the database tier

> **FIXED 2026-07-02** (R4): `REVOKE UPDATE, DELETE ON budget.journal_entries,
> budget.account_postings FROM shekel_app` -- append-only now binds at the database tier for the
> runtime role (SELECT/INSERT untouched; corrections stay appended reversals).  Cascade behavior
> was verified on the dev stack FIRST, as this finding required (all probes rolled back): with the
> revoke in force and acting as `shekel_app`, all four tamper forms fail with `permission denied
> for table`, a pay-period delete still cascades its 18 entries + 37 postings, and a transaction
> delete still SET-NULLs the entry back-link -- PostgreSQL executes referential actions as the
> table owner, empirically confirmed.  As-built, the posture has FOUR enforcement points because
> `entrypoint.sh` re-runs `init_db_role.sql`'s blanket `GRANT ... ON ALL TABLES` on every
> container start (a migration-only revoke would be silently undone at the next boot): the shared
> SQL lives in `posting_infrastructure.apply_ledger_append_only_privileges` (role-guarded, the
> `a5be2a99ea14` pattern), applied by migration `e3c23fadb21d` (existing DBs; downgrade re-grants,
> round-trip verified on dev), by `init_database.py` (the fresh-DB path stamps past the migration),
> by `build_test_template.py`, and re-asserted in psql form by `init_db_role.sql` itself
> (table-guarded, since it runs before migrations on a fresh boot).  Regression locks:
> `TestLedgerAppendOnlyPrivileges` (posture including a targeted-not-blanket control on
> `budget.accounts`; all four tamper rejections; CASCADE and SET-NULL disposal as the revoked
> role), with the `shekel_app_role` fixture now provisioning the exact runtime posture.  The
> raw-SQL reparent hole in the trigger's UPDATE arm is closed by the same revoke for the app role;
> the OWNER role can still tamper by construction (migrations need it) -- operator-SQL discipline
> remains the guard there.

The balanced trigger deliberately has no DELETE arm (`posting_infrastructure.py:111-118`, so
CASCADE disposal does not abort), and the ORM immutability listeners
(`journal_entry.py:370-423`) do not fire for raw SQL. Demonstrated: a psql single-leg DELETE
silently broke the trial balance. Additionally, the UPDATE arm checks only the NEW row's entry, so
a raw-SQL reparent of a leg (`SET journal_entry_id = other`) leaves the OLD entry unbalanced
unchecked. This matters for this project specifically because the operator routinely runs manual
SQL against these databases (prod-clone verifications, migration checks).

Cheap, high-value hardening: production runs the least-privilege `shekel_app` role, which today
receives blanket `SELECT, INSERT, UPDATE, DELETE` on budget tables
(`scripts/init_db_role.sql:94, 112`). The app never legitimately UPDATEs or DELETEs ledger rows
directly (disposal is FK CASCADE, which PostgreSQL executes as the constraint owner, not the
client role). `REVOKE UPDATE, DELETE ON budget.journal_entries, budget.account_postings FROM
shekel_app` would make append-only real at the DB tier for the running app, while migrations
(owner role) and cascades keep working. Verify the cascade behavior on the dev stack first.
Recommendation: R4.

### M2 (MEDIUM, reachable): pay-period reset wipes loan genesis entries despite its zero-settled gate

> **FIXED 2026-07-02** (`654c991`, R7): `reset_pay_periods` now re-posts the wiped genesis
> corrections onto the rebuilt schedule in the SAME transaction, after the repopulate step, via
> `loan_posting_service.resync_user_loan_postings(user_id)`.  It is scoped to the reset user (a
> new `resync_user_loan_postings` over a new `loan_loaders.load_loan_account_ids_for_user`, the
> per-user twins of the deploy-wide `backfill_all_loan_postings` / `load_all_loan_account_ids`
> pair) rather than the global sweep the recommendation named, because a reset is a single-user
> operation and must not reconcile other owners' loans inside its transaction.  The re-sync reuses
> the identical go-forward per-loan sync, so a re-posted correction is identical by construction;
> the loan's source facts (`LoanParams`, its `user_trueup` `LoanAnchorEvent` rows) carry no
> `pay_period_id` and survive the wipe, so the corrections re-derive and re-attribute to the new
> earliest period.  The overclaiming comment is corrected.  Regression locks:
> `test_pay_period_reset.py::TestResetResyncsLoanGenesis` (genesis nets -(anchor) before AND after
> the reset, re-attributed to the new period; proven to fail without the re-sync) plus the scoping
> pair `test_loan_posting_service.py::TestUserScopedResync` (the enumerator and the resync return
> only the given user's loans; a second owner's loan is never touched).  The walkthrough below
> describes the pre-fix code.

The reset gate counts settled transactions only (`pay_period_admin.py:474-476, 665-692`). Loan
opening/true-up entries exist independently of any settled transaction (a payment-less configured
loan posts its opening at params-create, `routes/loan/params.py:119` -> `_sync.py:113-146`). So a
user with a configured loan and zero settled rows is offered reset, and the wipe CASCADEs every
`loan_opening` / `loan_trueup` entry. Not permanent corruption -- `LoanAnchorEvent` and
`LoanParams` survive, so the next loan chokepoint or deploy backfill regenerates everything --
but until then every ledger-authoritative loan read hits the missing-OPENING sentinel and
degrades to the replay fallback, and the comment at `pay_period_admin.py:465-473` ("no posted
period is ever wiped") overclaims. Fix: call
`loan_posting_service.backfill_all_loan_postings()` at the end of `reset_pay_periods`, in the
same transaction; correct the comment. Recommendation: R7.

### M3 (MEDIUM, systemic): correction entries carry the period and date of the correction, not of what they correct -- and Step 5 will inherit this

> **RESOLVED 2026-07-03** (`feat/actuals-reporting`, C1-C13). The storage-level rule -- option (a)
> below -- was adopted for all three sources on 2026-07-02 (`2ffefa0`): a reversal entry carries the
> pay period of the postings it reverses and inherits the latest `entry_date` it reverses, so a
> plain revert nets in place for date-grouped reporting (facts 1 and 2 below fixed; a plain revert's
> entry_date is the original settle date, not the period-start fallback). The reader-contract HALF
> that was still open is now DONE: the ledger-wide attribution rule is recorded as the normative
> reader contract (`implementation_plan_actuals_reporting.md` Section 2 -- both the group-by-source /
> current-paid-date shape option (b) named below AND the option-(a) storage rule, used together) and
> implemented once in `ledger_report_service._attribution`, so every Step-5 reader dates a source by
> its CURRENT paid date (display-timezone, the L9 rule) and a correction by its `entry_date`, with
> hard-delete residue dropped whole. The statements oracle (`test_posting_ledger_statements.py`, C13)
> pins the residual-edge case this note flagged: a CROSS-YEAR hard-delete residue (original and
> reversal in different years, each netting to zero) is dropped from every window so no reader sees a
> half-entry, and the trial balance stays closed.

Three facts compound:
1. A reversal entry's `pay_period_id` is the source row's CURRENT period
   (`posting_service.py:770`) -- the H1 enabler.
2. A reversal entry's `entry_date` falls back to the period start because revert clears
   `paid_at` (`posting_service.py:151-173`) -- so a cross-year revert strands +/- amounts in two
   different years for any reader that groups by `entry_date`.
3. The project already hit this once and fixed it per-reader: the C10 tax-interest reader groups
   legs by payment and attributes by the CURRENT paid date precisely because entry-date grouping
   mis-stated both Schedule-A years (`implementation_plan_loan_read_switch.md`, Commit 10).

The C10 fix is correct but local. Step 5 (income statement, balance sheet by period/date) would
re-discover this bug class for cash and transfers. Decide the ledger-wide attribution rule ONCE,
before Step 5: either (a) stamp reversal/delta entries with the period/date of the postings being
reversed (read back from the ledger, the same discipline `_posted_net_by_account` already applies
to accounts), or (b) mandate the C10 pattern (group by source, attribute by current source state)
for every reporting reader and record it as the reader contract. Option (a) is the storage-level
fix and also dissolves H1's precondition. Recommendation: R2.

### M4 (MEDIUM): the loan parallel-run oracle's independence is convention-only after the read switch

> **RESOLVED 2026-07-02** (part (a) R5 + follow-up; part (b) R6): part (a) -- the
> convention-only independence -- is now mechanical AND
> path-complete. `TestResolverIsLedgerFree` fences the resolver reference with a
> source-AST import fence catching both `from pkg import ledger_mod` and
> `from pkg.ledger_mod import x` shapes (`ast.walk` reaches lazy in-function
> imports), a runtime read fence (defense-in-depth backstop; module-qualified reader
> calls monkeypatched to raise around `_resolver_balance`), and a negative control
> proving the AST fence bites. The initial R5 fence covered `loan_loaders` + the
> dynamically-enumerated `loan_resolver` package at FILE granularity but had a blind
> spot: `_resolver_balance` also runs through `loan_payment_service.load_loan_context`,
> and that module cannot be file-fenced because its read-switch functions read the
> ledger by design (follow-up review M-1). The follow-up closes it -- the mixed
> module is now fenced at FUNCTION granularity (every function except the read-switch
> allowlist `confirmed_loan_view` / `resolve_loan_seeded` / `resolve_account_loan`,
> top-level imports included), so a refactor wiring the ledger into `load_loan_context`
> or its sibling loaders now fails a test. A coverage guard also keeps the token
> denylist complete against a newly-named ledger reader (follow-up M-2). Part (b),
> the shared amortization kernel -- the walk and the replay both call
> `accrue_monthly_interest` / `rate_period_engine`, so a kernel bug moves both
> producers in lockstep and only hand-computed literals catch it -- is now CLOSED
> by R6: `test_arm_rate_step_matches_a_hand_computed_post_step_balance` pins the
> post-step ledger balance against a hand-computed literal (99,495.00) rather than
> a value read back from either producer, so a shared `rate_period_engine` bug
> that moved both fails the literal. (An escrow effective-date change is NOT a
> lockstep case -- VERIFIED by trace, closing the independent review's E-1: the
> resolver's balance replay ignores escrow entirely. `replay_schedule` iterates
> `confirmed_payment_dates` (dates only, never amounts) and `_replay_payment_row`
> computes `interest = accrue(balance, rate)`, `principal = period_pi - interest`,
> `new_balance = balance - principal` with no escrow term -- so the escrow-adjusted
> payment amounts in `ctx.payments` never touch the balance. An effective-dating
> bug therefore moves the WALK only, and the walk's escrow values are already
> pinned by `test_escrow_and_refund_reconcile_full_sweep`'s hand-computed literals,
> so M7's escrow bullet needs no parallel-run case.) M4 is now fully resolved.

The oracle correctly uses the un-seeded `resolve_loan` as its independent reference
(`test_posting_ledger_loan_reconciliation.py:461-493`), and the resolver package performs zero DB
access. But (a) production never runs un-seeded for an opened loan, so the reference is a
test-only configuration with no mechanical guard -- a future refactor that lets any resolver
input loader consult the ledger silently collapses the parallel run to a tautology and nothing
fails; and (b) the docstring's "shares none of its code path" overstates -- the walk and the
replay share the amortization kernel (`app.utils.money.accrue_monthly_interest`,
`rate_period_engine.period_for_date`, rate-period construction, the anchor loader, the shadow
predicate). A bug in the shared kernel moves both producers in lockstep; only hand-computed
literals catch it, and at the oracle level those exist only for fixed-rate, single-period,
flat-escrow fixtures. Recommendation: R5, R6.

### M5 (MEDIUM): the celebrated +$10 injection non-vacuity proof is manual, not executable

> **RESOLVED 2026-07-02** (R5): all three sub-items are now executable in CI. The
> +$10 walk injection is `test_walk_interest_injection_fails_the_value_checks_not_the_sweep`
> (patches `_walk.accrue_monthly_interest` only, so the resolver stays honest; asserts
> the parallel-run VALUE checks fail by exactly $10 and the structural sweep survives,
> the executable form of "9 of 11 failed"); the three `*_catches_a_tampered_*` proofs
> now drive the real `_assert_full_reconciliation` / `_assert_loan_reconciles` under
> `pytest.raises`, so a regression in the sweep helper itself is caught, not only in
> the inline re-derivation; and the linked-account sweeps (Steps 2/3) plus the loan
> completeness walk `assert` their enumerations non-empty, so an empty query cannot
> pass the sweep vacuously.

The "+$10 interest injection failed 9 of 11 tests" evidence lives in commit messages and
docstrings. It does not run in CI, so a later edit that weakens a value assertion loses the
oracle's teeth undetected. The cross-page oracle already shows the pattern to copy
(`TestSeamInjectionLock` monkeypatches and asserts the failure bites). Automate the walk
injection the same way. Also: the Step-2/3 tamper proofs re-derive comparisons inline instead of
driving the actual sweep helpers under `pytest.raises`, so a regression in the sweep helper
itself stays green; and the sweeps iterate enumerations that could silently go empty (assert
non-empty). Recommendation: R5.

### M6 (MEDIUM, latent -- must precede the scenario-clone feature): two multi-scenario gaps

1. A what-if scenario that holds the loan but has no payment in it gets no opening posting, reads
   `None`, and falls back to the replay (documented as M2 in the read-switch plan). Off-schedule,
   the baseline (ledger) and the what-if (replay) would show different balances for the same
   loan.
2. Scenario enumeration is "scenarios with a non-deleted payment shadow, plus baseline"
   (`_sync.py:41-72, 113-146`). A non-baseline scenario whose payments are later all soft-deleted
   drops out of the enumeration while its opening posting still answers -- loan-global changes
   (params edit, true-up, rate change) then skip it, leaving a frozen-but-authoritative ledger.
   Fix: enumerate by "has any loan-ledger posting" (distinct `journal_entries.scenario_id` on the
   linked ledger).

Both are unreachable today (only the baseline exists). Both must be resolved in the scenario-clone
design, not discovered after it ships. Recommendation: R8.

### M7 (MEDIUM): oracle-level coverage gaps where both producers could move in lockstep

> **RESOLVED 2026-07-02** (R6): all three gaps closed.
> - **ARM rate step** (`5c64107`):
>   `test_arm_rate_step_matches_a_hand_computed_post_step_balance` settles a $1,000
>   short payment on each side of a mid-history step to 12% and asserts the
>   post-step LEDGER balance against a HAND-COMPUTED literal (99,495.00), NOT a
>   value read back from either producer -- the one producer a shared
>   `rate_period_engine` bug cannot hide from, which closes M4(b)'s rate-kernel
>   lockstep. (The bullet's "escrow effective-date" alternative is not a lockstep
>   case; see the M4 annotation.)
> - **Biweekly collision** (`5c64107`, strengthened by `8d4fcb7`):
>   `test_biweekly_due_month_collision_reconciles_but_attribution_differs` settles
>   two on-schedule payments both due 2026-02-01 and pins that the BALANCE
>   reconciles three ways (ledger == resolver == reader -- the redistribution is
>   display-only when no rate change spans the shifted month) while the
>   ATTRIBUTION legitimately differs in TWO places. First (5c64107) the DISPLAY
>   rows: the reader dates both rows 02-01, the resolver replay redistributes the
>   second to 03-01. Then (`8d4fcb7`, closing the independent review's G-1) the
>   LEDGER per-period buckets -- the branch's own theme: each paydown lands in its
>   TRUE pay period, so the reader's per-period map reflects BOTH paydowns by
>   period 2 while the resolver's per-period view (payment 2 redistributed to
>   March) still shows only payment 1 at period 2's start. The distinct-month map
>   test cannot see that divergence; the collision is exactly where it surfaces.
> - **Transfer OUT of a loan** (`814b407`, `703af6a`): FORBIDDEN at creation
>   (developer-confirmed fork -- forbid, not bless): `_reject_transfer_out_of_loan`
>   rejects an amortizing loan as `from_account` at `create_transfer`, the sole
>   creation chokepoint (`from_account` is immutable after creation, and route /
>   template / recurrence all funnel through it). The income-only-shadows
>   assumption `_assert_loan_reconciles` relied on is now ENFORCED; the reader's
>   transfer-out KEEP arm is re-annotated as defense for a pre-guard legacy row.
>   Tests: a loan-as-source `create_transfer` raises and writes no transfer/shadow;
>   and (`703af6a`, closing the review's T-1) the recurring-template route path
>   now catches the guard's rejection and flashes gracefully rather than 500ing,
>   matching the one-time path -- the one defect the guard newly made reachable.
> The settled-row revert-and-move oracle case landed earlier with R2.

Never exercised at the reconciliation level (all verified absent by sweep):
- An ARM rate step or an escrow effective-date change through the parallel run (both are
  unit-tested per side, but a shared `rate_period_engine` / effective-dating bug moves the walk
  and the replay together -- one oracle case with a hand-computed post-step literal closes it).
- A biweekly due-month collision (two payments in one due month) through the reconciliation --
  the one place reader and resolver attribution can legitimately disagree
  (`_redistribute_to_distinct_months` is resolver-only), flagged since Step 4 (C4 note M2) but
  never pinned.
- A transfer OUT of a loan: `transfer_service` does not forbid it, `_assert_loan_reconciles`
  assumes income-only shadows, and the history reader ships a live classification branch for it
  (`_reader.py:556-560`) that no test has ever executed. Either forbid the flow or test the
  branch.
Recommendation: R6.

### Lower-severity findings (grouped)

- **L1:** pylint silently ignores unknown `--fail-on` symbols, and the gate list is maintained in
  five places (`ci.yml:147,159`; `.pre-commit-config.yaml:28,43`; `post-edit-python.sh:67-69`).
  A renamed checker symbol would drop its hard gate without an error; today the 10.00 floor
  backstops it. Single-source the list or assert list-equality in the checker tests.
- **L2:** fence allowlist hygiene: `growth_engine` is allowlisted for W9906 but calls zero fenced
  producers (unused exemption = attack surface); the genesis-reader allowlist grants all of
  `loan_payment_service` while the documented intent is `confirmed_loan_view` only -- keep that
  module small.
- **L3:** the date-precise loan scalar (`balance_at.balance_at` AMORTIZING branch) does not see a
  true-up dated after the last confirmed payment until the next payment row -- a
  card-versus-year-end divergence of the true-up delta. Acknowledged as the retained "due-basis"
  semantic; now cheaply fixable (route the confirmed region to `confirmed_loan_balance_at` for
  `as_of <= today`) but it changes year-end debt-progress semantics -- a deliberate decision, not
  a drive-by fix.
- **L4:** the anchors-plus-payments merge-order rule is spelled twice (`_walk.py:311-326` and
  `_reader.py:806-816`) with cross-references but no shared code; extract the merge key into
  `_common`.
- **L5:** `net_worth_kernel.py:634` imports the private `loan_posting_service._reader` submodule
  directly (documented cycle workaround; an encapsulation blemish -- re-export through a leaf or
  accept and document).
- **L6:** the cross-page oracle counts one producer twice: `_grid_value` and
  `_accounts_checking_value` are byte-identical `cash_balance_map` calls, so the "6 surfaces" are
  5 producers, and routes get only status-200 checks -- a route that drifted onto a different
  producer would pass. Wire at least one reader to a route response value.
- **L7:** the cash oracle's counter-ROUTING check resolves the expected category account with the
  same `get_or_create_category_ledger_account` the writer uses -- a deterministic routing bug
  passes both sides. Derive the expectation with a test-authored `(user, category, class)` query.
- **L8 (doc drift, three instances found):** the two `import-outside-toplevel` rationales in
  `loan_payment_service.py` (~:551, ~:590) claim "loan_resolver imports from this module" --
  no longer true (docstring references only; the lazy imports may be removable); the
  "cascade-imbalance impossibility argument" in `ledger_account.py:141-155` /
  `journal_entry.py:255-262` is stale post-Step-3/4 (the operative guard is route Guard 5 --
  `archive_helpers.account_has_ledger_postings`); the reset comment overclaim (M2).
- **L9 (policy edge):** `entry_date` is the UTC civil date by design
  (`posting_service.py:128-148`) while the user's display timezone is America/New_York. A settle
  clicked ~8pm ET on Dec 31 books Jan 1 UTC, shifting tax-year attribution (mortgage interest
  now; the whole income statement under Step 5). Decide once: accept (document in the tax
  surface) or attribute tax-year figures by display-timezone civil date.
  **DECIDED 2026-07-03 (developer): attribute tax-year figures by the DISPLAY-timezone
  (America/New_York) civil date.** Storage stays UTC; the conversion is a presentation-boundary
  concern at the tax-reporting readers only (the same `app/utils/dates` / `local_datetime`
  discipline the app already uses for display). Implementation is a separate follow-up item (not
  in the R10 housekeeping batch): the mortgage-interest Schedule-A reader now, and every Step-5
  income-statement/tax figure when Step 5 lands. Until then this is a recorded policy, not yet
  code.
  **IMPLEMENTED (Schedule-A half) 2026-07-03, Step-5 C1** (`feat/actuals-reporting`): the new
  `app/utils/dates.to_display_civil_date` helper (composes `to_display_date`; period-start
  fallback mirrors the entry dating) now drives `confirmed_loan_interest_in_year`; storage stays
  UTC. Boundary tests pin an 8:05pm-ET Dec-31 settle to the earlier year at the helper, the loan
  reader, AND the year-end hybrid wiring. The income-statement half lands with the Step-5
  reporting readers (`implementation_plan_actuals_reporting.md`, reader contract C-3).
- **L10:** an envelope whose credit entries exceed its effective amount would flip the sign of
  `effective - credit_sum` (an expense becoming net cash inflow). Nothing rejects or pins the
  shape; add a guard or a pinned test.
- **L11:** legacy origination `LoanAnchorEvent` rows are dead data kept append-only, correctly
  ignored by the single loader (`loan_loaders.py:110-120` filters USER_TRUEUP); risk is only a
  future consumer querying the table without the filter. The single-loader convention is the
  guard; keep it.

### Informational (architecture boundaries, all documented somewhere, gathered here)

- **The modeling boundary on interest and escrow:** interest accrues per payment event, not per
  time -- a skipped month books no interest, so a loan in deferment/forbearance flat-lines in the
  ledger while the real loan grows; and every payment is attributed the full configured escrow,
  so a $100 token payment books 616.99 escrow + full interest and a large negative principal,
  where a real lender would hold it in suspense. Internally consistent, cash-correct, and the
  true-up remains the correction tool; the D13 statement-split override is the eventual fix.
  Worth one paragraph in the architecture-of-record so it is a stated boundary, not a surprise.
- **The trial balance does not close app-wide** until cash gets opening-equity postings (Step 5):
  loans sum to absolute balances, cash to deltas. Documented; the standing trap is any future
  reader assuming `account_posting_total` is a balance. The asymmetry argues for finishing Step 5
  rather than pausing indefinitely.
- **Two backfill philosophies coexist:** raw-SQL-in-migration (Steps 2-3, duplicating the effect
  formula in SQL, oracle-guarded) versus deploy-hook service-reuse (Step 4+, backfill ==
  go-forward by construction). The second is strictly better; standardize on it for Step 5.
- **Equity accounts are write-only** until Step-5 reporting reads them; integrity rests on the
  balanced trigger + construction + the injected-leg trial-balance test. Acceptable staging.
- **The grid/obligations panel pointed at a loan account shows the cash-flow view** (deliberate,
  documented INTEREST-only accrual design, `balance_at.py:702-733`).
- **`amount_overrides` None-semantics differ by kind** (PLAIN auto-builds live income; INTEREST
  uses stored) -- the seam documents and normalizes it inside `grid_balance_view`
  (`balance_at.py:906-917`) rather than fixing the producer asymmetry. Acceptable; a future
  producer-contract normalization would delete a lot of documentation.
- **Balance-bearing rich functions stay outside the W9906 fence by the documented SRP line**
  (`resolve_loan`, `project_balance`, `confirmed_loan_history_rows`,
  `confirmed_loan_interest_in_year`) -- a consumer could re-derive a balance from them without a
  checker firing. That judgment line is assigned to review, not the machine; remember it when new
  callers appear.

---

## 4. The seven questions, answered directly

**Is this DRY?** Yes, with named residuals. One balanced-write path for every source; one
accrual helper shared by split and replay (extracted when a reviewer caught the copy); shared
eligibility predicates and loaders; the unified single-walk sync. Residuals: the merge-order rule
spelled twice (L4), the Step-2/3 backfill SQL duplicating the Python effect formula (guarded, and
superseded by the Step-4 pattern), the five-place gate list (L1), one duplicated oracle reader
(L6).

**Is this SOLID?** Largely yes, and demonstrably so where it was hard: the status seam extraction
(`status_seam.py` as a leaf module breaking a real import cycle), the two-domain-seams-over-
shared-primitives decision (rejecting a type-switching merged seam), the reader/writer split
inside `loan_posting_service`, the loan_loaders leaf extraction as a genuine layering fix.
Dependency direction (consumers -> seam -> engines) holds. Blemishes: the private `_reader`
reach-in (L5), the cross-module use of underscore-prefixed primitives as package-private
convention, and module-granular fence allowlists standing in for function-granular intent (L2).

**Is this fully normalized?** Yes where it counts, with deliberate and documented
denormalizations: `scenario_id`/`user_id` on journal entries (tenancy/isolation), display-name
snapshots on ledger accounts (posted history must survive renames), `is_fallback` +
`kind_id` making the four row kinds storage-enforced rather than NULL-pattern conventions.
Temporal escrow chose real normalization (range-dated versions) over a per-payment snapshot. The
anchor concept has three artifacts (immutable `LoanParams` origination, `user_trueup` rows as
source documents, derived genesis postings) -- coherent under the fact-versus-derivation rule,
with reconcile-to-target keeping derived in sync with source in the same transaction.

**Is this robust?** For amounts: yes, three layers deep (service pre-check, deferred DB trigger,
oracles), and the live experiments bear it out. *(Since strengthened: R6 closed the oracle's
shared-kernel lockstep blind spot with a hand-computed ARM-step ledger literal and pinned the
biweekly-collision reader/resolver attribution -- see the M4 and M7 annotations.)* For period/date
attribution: no -- that is
exactly where H1, H2, and M3 live, and no layer enforces it. *(Since fixed: the R1/R2 remediation
adopted the per-period reconcile and attribution rule and added the `LEDGER_POSTINGS` lock, so
attribution now has a storage-level rule plus a classifier gate enforcing it -- see the header
note and the per-finding annotations.)* For raw-SQL operator error: one
demonstrated gap (M1) with a cheap DB-tier hardening available. *(Since fixed: R4 applied that
hardening -- see the M1 annotation.)* Concurrency: version_id
optimistic locks on the transfer/transaction paths; the loan-global paths rely on
single-transaction reconcile-to-target (documented as acceptable for a solo user).

**Is this maintainable for a solo developer?** The documentation and gate culture is the best
part of this codebase: every invariant is written down next to the code that carries it, and the
checkers turn conventions into build failures. Two honest risks. First, conceptual surface: seven
fence checkers, four ledger row kinds, three sync services, two views in one seam, and a
walk-with-resets -- returning to this cold in a year is viable only because the docstrings are
excellent, which leads to the second risk: doc drift. This review found three instances of
load-bearing comments that are no longer true (L8). The docs ARE part of the machine here; a
periodic drift pass (or extending the checkers to verify claims like allowlist accuracy) is
maintenance, not polish.

**Is this future-proof and extensible?** The fence plus the oracles make extension structurally
safe -- a new surface cannot re-derive a balance, and a new posting source reuses one write path.
Three things must land before their triggering features: the multi-scenario gaps before scenario
clone (M6), the correction-dating rule before Step 5 reporting (M3), and the D13 statement-split
override before promising lender-exact histories. The revisit-triggers list in the fitness doc
(multi-currency, external import, regulated reporting) remains the honest boundary of the design.

**Is this financially correct?** For every dollar amount: yes, to the penny, verified live on
real data (off-schedule capture, true-up corrections, production-wide reconciliation, three-way
producer parity). For WHEN dollars are attributed: two real defects (H1 period-stranding, H2
early-settle mis-split), one systemic semantics gap (M3), and one policy edge (L9 UTC tax-year).
*(Since fixed: H1 and H2 are remediated and live-verified, and M3's storage half is adopted --
see the header note; L9 and M3's reader-contract half remain open.)*
For WHAT the model claims about the outside world: internally consistent but payment-event-driven
(the deferment/suspense boundary above) -- a modeling choice to document, not a bug.

---

## 5. Re-evaluation of the decisions (no anchoring)

Each major decision re-examined from scratch against the alternatives it beat.

- **Option D (double-entry confirmed ledger + live projections) over Level-1-only.** Level 1
  alone would have fenced the display-divergence bug class. It would NOT have given you:
  off-schedule payments captured without true-ups (demonstrated working), the end of the
  per-payment true-up band-aid, the sum-to-zero self-check, or an audit-grade confirmed history.
  Given this app manages your real finances and the loan true-up history was a recurring source
  of wrong balances, the extra investment bought real correctness, not rigor theater. The
  fitness doc's own rejection of E/F (materializing projections) remains exactly right. VERDICT:
  correct call. The one caveat: Option D's value is only fully realized when Step 5 closes the
  cash asymmetry -- see R9.
- **Genesis (opening-equity + sum of postings) over the anchor-based reader.** The anchor-based
  intermediate would have kept a read-time boundary filter -- the precise pattern the root-cause
  doc identified as the bug generator. Genesis made the pre-anchor problem an algebraic identity
  instead of a cleanup. VERDICT: correct, and the more-correct-design-sequenced-safely discipline
  (write-only Step 4, parallel-run, then flip) is the pattern to repeat.
- **Real split from actual cash (v2) over the contractual mirror (v1).** The adversarial review
  that forced this rewrite was the single highest-value review in the arc. VERDICT: correct.
- **Posting at `is_settled` (Paid/Received), not at Settled.** Verified: nothing ever assigns
  SETTLED, so the alternative was a dead pilot. VERDICT: correct.
- **Per-category ledger accounts now (D1)** rather than one Income/one Expense. Buys the Step-5
  income statement with no repoint. VERDICT: correct.
- **Configured escrow, effective-dated (temporal escrow), no inflation on recorded values.**
  The normalization choice was right and the on-schedule cancellation invariant
  (`escrow_split == escrow_built`) is well-argued. VERDICT: correct.
- **Escrow as Expense (D8), impound-asset deferred; statement-split override deferred (D13).**
  Both acceptable staging; D13 is the eventual answer to the token-payment modeling boundary.
- **Born-Projected (create rule)** -- right rule, incomplete enforcement (H3). *(Since
  mechanized: R3 made it a W9907 machine rule -- see the header note and the H3 annotation.)*
- **Keeping the presentation gates after Level 1** (fitness doc refinement 2) -- right;
  presentation windows are not balance semantics.
- **The C11 forks decided by the agent under standing directive -- RATIFIED 2026-07-03 (developer):**
  (a) retire the ORIGINATION anchor write but KEEP `user_trueup` rows as the operator's source
  document -- on review this is not a compromise but the correct reading of fact-versus-
  derivation: the true-up is a dated operator assertion (a fact) from which the correction
  posting is derived and self-heals; retiring it would have re-invented the same store under a
  new name. RATIFIED. (b) The six-commit C11 decomposition -- mechanically sound,
  independently green, reviewed per commit. RATIFIED. Both were already live in prod (PR #52);
  ratification confirms them as the standing design, no code change.

**What should have been done differently** (two things, both recoverable now):
1. When the first reversal entry shipped (Step 2, Commit 4), the ledger needed a stated rule for
   how corrections are dated and period-attributed. It was never written, and H1/M3/the C10 bug
   are all downstream of that omission. Adopt the rule now (R2).
2. The split walk's period-begun eligibility bound was inherited from the resolver's replay
   semantics into the WRITE path, where it does not belong -- the write side should mirror the
   cash posting's settled-only gate (H2/R1).

---

## 6. Recommendations, prioritized

| # | Priority | Action |
|---|----------|--------|
| R1 | **DONE 2026-07-02** (`8ad3a81`) | Fix H2: make the split walk include settled payments regardless of period-begun (write-side gate = settled only, matching the cash leg). Add the "early settle, then time passes" oracle case that would have caught it. As-built: `_settled_income_shadows` (write side, unbounded) split from `_confirmed_shadows_through` (display side, kept); tax-hybrid partition fix added (`load_settled_payment_due_months`); oracle + unit + tax regression locks, all mutation-proven; live-verified on the real Mortgage. |
| R2 | **DONE 2026-07-02** (`2ffefa0`) | Adopt the correction-attribution rule: reversal/delta entries carry the pay period (and, where meaningful, the date) of the postings they reverse, read back from the ledger. This dissolves H1's precondition and the M3 class. Defense-in-depth for H1: the truncate/regenerate classifier also refuses (or first re-attributes) any to-delete period whose journal entries do not net to zero per ledger account. As-built: per-(account, period) reconcile in `posting_service` (`_posted_by_period`/`_reconcile_periods`; syncs return entry lists) and the loan payment reconcile; `PeriodLockReason.LEDGER_POSTINGS` gate (blocks non-netting periods, allows self-cancelling pairs), mutation-proven; `posting_service` size-split into `posting_reads.py`. M3's reader-contract half for Step-5 readers is NOT covered here and stays open. |
| R3 | **DONE 2026-07-02** (`ad24d84`) | Mechanize the status fence's blind spots (H3): W9907 extensions for `Transaction(`/`Transfer(` ctor `status_id=` kwargs, `setattr(x, "status_id", ...)`, and `"status_id"` keys in `.update()`/`.values()` dicts -- all zero-violation today. Optionally add the import-level fence (flag `ImportFrom`/aliasing of fenced producers) to close the alias class. As-built: all three extensions plus the import fence landed. Ctor kwargs are allowed only for recognizably born-Projected values (`ref_cache.status_id(StatusEnum.PROJECTED)` / a `projected_id` name or attribute -- the only shapes in-tree); everything else fails closed. Bulk writes flag on the key/keyword regardless of value (they bypass paid_at and verify_transition even for Projected). No new message IDs, so the five-place `--fail-on` list (L1) is untouched. 108 checker tests; probe-verified through `.pylintrc`; app/ and scripts/ hold 10.00. |
| R4 | **DONE 2026-07-02** | DB-tier append-only (M1): `REVOKE UPDATE, DELETE` on `budget.journal_entries` + `budget.account_postings` from `shekel_app` (verify FK cascades on the dev stack first; grants live in `scripts/init_db_role.sql`). As-built: cascades verified live first (rolled back); shared role-guarded SQL in `posting_infrastructure`; migration `e3c23fadb21d` (downgrade re-grants; round-trip verified on dev) + `init_database.py` + `build_test_template.py` + the every-start re-assert in `init_db_role.sql` (which would otherwise re-open the hole at each boot via its blanket GRANT); `TestLedgerAppendOnlyPrivileges` locks posture, tamper rejection, and owner-executed CASCADE/SET-NULL disposal. |
| R5 | **DONE 2026-07-02** | Make the oracle teeth executable (M4/M5): automate the +$10 walk injection as a negative-control test; drive the tamper proofs through the real sweep helpers with `pytest.raises`; add a mechanical guard that `loan_resolver`/`loan_loaders` stay ledger-free (import check or raising monkeypatch in `_resolver_balance`); assert sweep enumerations non-empty. As-built (all in the three reconciliation oracles): `test_walk_interest_injection_fails_the_value_checks_not_the_sweep` injects $10 into `_walk.accrue_monthly_interest` only (the resolver's `rate_period_engine` binding stays honest), proving the parallel-run VALUE checks fail by exactly $10 while the structural sweep -- an accounting identity -- survives; the three `*_catches_a_tampered_*` proofs now also drive the real `_assert_full_reconciliation` / `_assert_loan_reconciles` under `pytest.raises`, so a regression in the sweep helper itself is caught; `TestResolverIsLedgerFree` fences the resolver stack three ways -- a source-AST import fence over `loan_loaders` + the dynamically-enumerated `loan_resolver` package (catching both `from pkg import ledger_mod` and `from pkg.ledger_mod import x` shapes), a runtime read fence monkeypatching every ledger reader to raise around `_resolver_balance`, and a negative control proving the AST fence bites; and the linked-account sweeps (Steps 2/3) and the loan completeness walk now `assert` their enumerations non-empty so an empty query cannot pass vacuously. Full suite green. This closes M5 and M4(a); M4(b) (the shared amortization kernel moving both producers in lockstep) is coverage, still owed by R6. **Follow-up hardening (same-day review of `bb567e9`):** M4(a)'s file-only fence had a blind spot -- `_resolver_balance` also runs through `loan_payment_service.load_loan_context`, an unfenceable mixed module (its read-switch functions read the ledger by design) -- now closed by fencing that module at FUNCTION granularity (all functions except the read-switch allowlist, top-level imports included); a coverage guard keeps the token denylist complete against a novel-named reader; the runtime read fence is relabeled a defense-in-depth backstop with its module-qualified-only scope stated honestly; and the tamper proofs gained `match=` so they pin the intended invariant. |
| R6 | **DONE 2026-07-02** (`5c64107`, `814b407`, `8d4fcb7`, `703af6a`) | Close the lockstep coverage gaps (M7): one ARM-step and one biweekly-collision parallel-run oracle case; either forbid transfers OUT of a loan or test the reader's rule-3 branch. As-built: `test_arm_rate_step_matches_a_hand_computed_post_step_balance` (a HAND-COMPUTED post-step ledger literal 99,495.00, the shared-`rate_period_engine` teeth that also closes M4(b)) and `test_biweekly_due_month_collision_reconciles_but_attribution_differs` (balance reconciles three ways; the reader/resolver attribution difference pinned in TWO places -- the display rows AND, since `8d4fcb7` closing the review's G-1, the LEDGER per-period buckets, the branch's own theme) landed as test-only oracle cases. The transfer-OUT fork was decided FORBID (developer-confirmed): `_reject_transfer_out_of_loan` rejects an amortizing loan as `from_account` at the `create_transfer` chokepoint, so the income-only-shadows invariant is enforced not assumed; the reader KEEP arm is re-annotated as legacy-only defense; a rejection test writes no transfer/shadow. A follow-up (`703af6a`, review T-1) fixed the one defect the guard newly made reachable -- the recurring-template route path now catches the rejection and flashes gracefully instead of 500ing, matching the one-time path. The settled-row revert-and-move oracle case landed earlier with R2 (`TestRevertAndMoveReconciles` + the loan/transfer/transaction unit locks). All four independent-review findings on the R6 commits (G-1, T-1, D-3, E-1) are addressed: G-1/T-1 by code, D-3 by the accurate biweekly wording above, E-1 by the verified escrow trace in the M4 annotation. |
| R7 | **DONE 2026-07-02** (`654c991`) | Pay-period reset: re-run the loan backfill inside `reset_pay_periods`' transaction and fix the overclaiming comment (M2). As-built: a PER-USER re-sync (`loan_posting_service.resync_user_loan_postings` over `loan_loaders.load_loan_account_ids_for_user`, the scoped twins of the deploy-wide `backfill_all_loan_postings` / `load_all_loan_account_ids`) rather than the global sweep, since a reset is single-user and must not reconcile other owners' loans in its transaction; it reuses the identical go-forward sync (a re-posted correction is identical by construction), runs after the repopulate step in the same transaction, and re-attributes the surviving source facts' corrections onto the new schedule. Regression: `TestResetResyncsLoanGenesis` (fails without the re-sync) + the `TestUserScopedResync` scoping pair. (The TRUNCATE variant -- wiping a current-period true-up correction -- was already closed by R2's `LEDGER_POSTINGS` gate; reset does not use the classifier, so this re-sync closed its half.) |
| R8 | BEFORE scenario clone | Resolve both multi-scenario gaps (M6): enumeration by ledger postings, and the what-if None-fallback policy. |
| R9 | **DONE 2026-07-03** (`feat/actuals-reporting`, C1-C13) | Step 5 built explicitly: every non-loan account posts anchor-equity opening / true-up corrections (write side, `account_posting_service`, via the deploy-hook backfill pattern and the R2 dating rule), so the trial balance closes app-wide and the equity accounts are consumed; an income statement + balance sheet with a two-part tie-out read that closed ledger (`ledger_report_service` + two `/analytics` tabs + CSV). Reads for the app's balance screens INTENTIONALLY stay on the `balance_at` seam (the Step-5 non-goal: no cash read switch), so the loan-absolute / cash-projection asymmetry on the DISPLAYED balance is a documented steady state -- the ledger now records the confirmed cash position, but the seam remains the projection authority. Oracle-pinned by `test_posting_ledger_statements.py` (C13). |
| R10 | HOUSEKEEPING | Doc-drift batch (L8): the two stale import rationales, the stale impossibility argument in the two model docstrings, the reset comment. Single-source the `--fail-on` list (L1). Trim `growth_engine` from the W9906 allowlist (L2). De-duplicate the cross-page grid/checking readers or wire one to a route value (L6). Decide the L9 timezone policy for tax attribution and the L10 over-credit guard. Ratify the two C11 forks (Section 5). Split `tools/pylint/shekel_checkers.py` into a package (added 2026-07-02: the R3 extension pushed the plugin past pylint's default module size -- ungated C0302, 1145/1000 lines -- and the file also carries pre-existing E0011/W0012 meta-noise from its own prose comments about directives plus one `too-many-locals`; the split is a gate-infrastructure change, so verify `.pylintrc` `load-plugins`, the per-edit hook, pre-commit, and CI all still load the plugin, and consider giving `tools/pylint/` its own enforced floor once clean). Also split `app/services/transfer_service.py` (added 2026-07-02: the R6 transfer-out guard wiring left it at exactly 1000/1000 lines, its `too-many-lines` cap -- the guard body itself went into the already-extracted `_transfer_loan_posting.py` to stay under, but the next single-line addition will trip C0302; a further extraction, e.g. the mutation/validation helpers, restores headroom). |

> **R10 in progress (2026-07-03).** Housekeeping is being worked sub-item by
> sub-item, each its own commit:
>
> - **`transfer_service.py` split -- DONE** (`a8eb95f`): the three
>   validate-and-load guards (`_validate_positive_amount`,
>   `_get_transfer_or_raise`, `_get_shadow_transactions`) moved to a new
>   `_transfer_validation.py` sibling (the `_transfer_ownership` /
>   `_transfer_loan_posting` pattern), taking the module to 874/1000 lines. The
>   three movers write no `status_id` and construct no `Transaction`, so the
>   W9906/W9907 fence allowlists are untouched and the status-mirroring appliers
>   (`_build_shadow`, `_apply_status_change`, `_apply_actual_amount`) stayed in
>   the parent module.
> - **L1 `--fail-on` list -- DONE** (`7c15303`):
>   `tools/pylint/tests/test_fail_on_gate_consistency.py` pins the list to an
>   exact bijection with the registered checker messages AND asserts all five
>   executable gate locations carry the identical list, so a renamed or
>   mistyped symbol (which pylint silently ignores at `--fail-on`) now fails a
>   test. Both assertions proven non-vacuous. Chosen over physical
>   single-sourcing because GitHub-Actions YAML + pre-commit YAML + shell cannot
>   share one fragment without a codegen step.
> - **L8 doc-drift -- DONE** (`ee7fb4b`, `1cc03b6`): (1) the
>   `loan_payment_service` lazy-import rationales were not merely stale but
>   vestigial -- `loan_resolver` no longer imports the module (proven: importing
>   it pulls in zero `loan_payment_service`), so `loan_resolver` was promoted to
>   a top-level import, the four dead lazy imports plus the redundant
>   `TYPE_CHECKING` block removed, and `confirmed_loan_view`'s remaining
>   `loan_posting_service` lazy import RE-documented with its REAL reason -- the
>   `TestResolverIsLedgerFree` fence, which scans this module minus its
>   read-switch functions and a top-level ledger import would fail; (2) the
>   cascade-imbalance impossibility argument in `ledger_account.py` /
>   `journal_entry.py` was rewritten to name route Guard 5 as the operative
>   guard (a settled transfer's postings OUTLIVE the transfer, breaking the old
>   shadow-RESTRICT premise); (3) the reset-comment overclaim was ALREADY
>   corrected by R7 (`654c991`).
> - **L2 `growth_engine` allowlist trim -- DONE** (`53bb7a37`): verified it
>   calls zero fenced producers and zero loan-ledger readers, then removed it
>   from `_BALANCE_SEAM_MODULES` (and thus `_LOAN_LEDGER_READER_MODULES`) plus
>   the W9906 message description and the test docstring; W9906 on the file is
>   clean without the exemption, proving it was dead.
> - **L10 over-credit envelope guard -- DONE** (`e329bbbb`): the sign flip
>   (credit entries exceeding `effective`, turning an expense into a net cash
>   inflow) is structurally impossible -- `TransactionEntry.amount` is
>   `CHECK (amount > 0)` and a settled envelope's `actual_amount` = sum of ALL
>   entries, so `effective - Sigma(credit) = Sigma(debit) >= 0` always. Pinned
>   with a test (debit $1 / credit $99, actual $100 -> cash leg -1.00, never
>   positive) that fails if `actual` ever drifts below the credit sum or the
>   positive-amount CHECK is relaxed. A pinned test, not a runtime guard, is the
>   right fix: the invariant already holds by construction and a guard would be
>   dead code (rule 13).
> - **`shekel_checkers.py` package split -- DONE** (`6a06d4c`): the R3
>   status-fence extension had pushed the single module to 1145/1000 lines
>   (ungated C0302). Split one-checker-per-module (`money` / `refname` /
>   `disable_rationale` / `loan_balance` / `balance_seam` / `status_bypass`)
>   over a shared `_common` leaf holding the three cross-checker AST primitives
>   (`_is_string_const`, `_called_name_in`, `_module_in_allowlist`); the package
>   `__init__` re-exports the six checker classes, the five module/producer
>   sets, and `register()`, so the plugin's import surface and entry point are
>   byte-identical to the pre-split module -- `.pylintrc` (`load-plugins`),
>   both checker test files (`from shekel_checkers import ...`;
>   `inspect.getmembers`), and pylint itself are unchanged (a pure internal
>   refactor, not an interface change). The same pass cleared the two other
>   latent findings the file carried: the E0011/W0012 meta-noise (the
>   `_RATIONALE_MARKER` / `_DISABLE_RE` prose comments spelled the literal
>   lowercase pylint-colon token, which pylint's own inline-option parser
>   misread as directives -- reworded to avoid it; the regex string keeps the
>   `disable=` literal, being a string not a comment token) and the R0914
>   too-many-locals in `process_module` (extracted `_read_source` /
>   `_scan_comments`). Package now 10.00/10 (was 9.54). Verified the plugin
>   still loads AND enforces: 110 checker unit tests + the gate-consistency
>   test green, a `shekel-decimal-from-float` probe fires end-to-end, and the
>   exact CI gate `pylint app/` and `pylint scripts/`
>   (`--fail-on=E,F,shekel-*`) both exit 0. The four references the split made
>   stale (CLAUDE.md, the ci.yml gate comment, two test docstrings) were
>   updated in the same commit. The audit's "consider giving `tools/pylint/`
>   its own enforced floor once clean" is now addressed -- see the next bullet.
> - **`tools/pylint/` enforced floor -- DONE** (`d356160`): the custom checkers
>   ARE the enforcement machinery, so they now hold the same 10.00/10 floor they
>   impose on `app/` and `scripts/` -- the gate that would have caught the R3
>   status-fence extension silently dropping the plugin to 9.54. Added a `Lint
>   checker package with pylint` step in `ci.yml` (5a2) and a `pylint-checkers`
>   pre-commit hook, both
>   `pylint tools/pylint/shekel_checkers/ --fail-under=10 --fail-on=E,F,shekel-*`.
>   Scoped to the `shekel_checkers/` package, NOT all of `tools/pylint/`: the
>   `tests/` subtree is 9.84 and stays out of general pylint scope (ratified
>   decision #1), covered by the `shekel-checker-tests` pytest hook. The
>   canonical `--fail-on` list is spelled identically, so the existing L1
>   gate-consistency test now validates the two new occurrences for drift with
>   no test change; its location docstring is updated (five -> seven executable
>   locations). Proven non-vacuous: the gate exits 0 on the package and exits 24
>   on the 9.84 `tests/` dir.
> - **L6 cross-page oracle route-render lock -- DONE** (`9b35483`, ported to
>   dev's unified cash-detail page when the r10 branch merged into dev): the
>   cross-page balance-equality oracle counted six surfaces but two readers
>   (`_grid_value`, `_accounts_checking_value`) were byte-identical
>   `cash_balance_map` calls -- really five producers -- and every reader
>   re-called a service/seam function and reconstructed what the route SHOULD
>   render, while the routes got only a 200 liveness check. A route that drifted
>   onto a different producer, or a template mis-render, would pass undetected
>   (the reader hardcodes the correct call; the 200 says nothing about the
>   value). Fix (the audit's "wire at least one reader to a route response"):
>   `_accounts_checking_value` now drives the real `GET /accounts/<id>/details`
>   (dev's Fable-5 overhaul `884c2e04` merged the former checking-detail page
>   into the unified `cash_detail.html`; `/checking` is now a redirect alias)
>   and reads the balance the template rendered, off a new raw-Decimal
>   `data-current-balance` hook on the cash-detail hero (not the formatted
>   `money()` text, so it is robust -- not the fragile scraping an earlier
>   revision rejected). This makes the cash-detail surface genuinely distinct
>   (fixes the double-count) AND locks the route-render path (fixes the gap); the
>   stale "HTML parsing adds no coverage" docstring is corrected. Non-vacuity: a
>   new negative control
>   (`test_route_render_lock_catches_a_cash_detail_route_misrender`) monkeypatches
>   ONLY the cash-detail route's `_current_period_balance` to a divergent Decimal
>   and asserts the equality fails naming the `accounts_checking` surface,
>   proving the reader reads the actual render, not a reconstruction. Verified on
>   dev: 21 cross-page oracle tests pass and the full suite is green (6896
>   passed); djlint / jinja-syntax / the three pylint floors clean. Scope: one
>   route wired (the audit's "at least one"); grid / savings / dashboard keep
>   their 200 liveness checks.
> - **Decisions resolved 2026-07-03 (developer):** L9 tax-year attribution ->
>   DISPLAY-timezone civil date (recorded under the L9 finding; implementation
>   is a separate follow-up, not this batch), and both C11 forks RATIFIED
>   (Section 5).
> - **Remaining implementation:** only the decided-but-unimplemented L9
>   display-tz attribution (a Step-5-era follow-up, not a fresh finding).
>   The L2 note that the genesis-reader allowlist still grants
>   all of `loan_payment_service` (intent: `confirmed_loan_view` only) is left
>   as-is: function-granularity W9906 is a larger checker change, and the R5
>   follow-up already fences that module at function granularity for the
>   resolver-free property.

---

## 7. Experiment log (for reproducibility)

All experiments ran 2026-07-02 against the dev database (`shekel-dev-db`, migration head
`f3d6b1a8c2e4`, 118 entries / 241 postings / trial balance 0.00 before and after). Mutating
probes wrapped every write in one session with `db.session.commit` stubbed to `flush` where a
service commits internally, and ended with `ROLLBACK`; final counts and transfer statuses were
re-verified identical. Probe scripts: `offschedule_probe.py`, `trueup_probe.py`,
`early_settle_probe.py` (session scratchpad; reproducible from the descriptions in Sections 2.1
and 3).
