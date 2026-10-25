> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Financial Calculation Audit -- Remediation Commit Prompts

- Companion to: `docs/audits/financial_calculations/remediation_plan.md`
- Purpose: one ready-to-paste session prompt per commit (37 total) so each commit can be executed in
  its own fresh session.
- Audience: future Claude Code sessions (and the developer reading what each session was asked to
  do).

## How to use this document

1. Wait until every prerequisite commit listed under "Prereqs on dev" has been merged to `dev` (and
   `main`, via the PR-gated workflow in CLAUDE.md). Each prompt depends only on the state of `dev`,
   not on any prior session context.
2. Start a fresh Claude Code session at the project root with `dev` checked out.
3. Copy the entire fenced block under the commit's heading. Paste it as the first message in the new
   session. Do not edit it.
4. The session will read the canonical plan section for this commit, re-verify against current code,
   do the work, run the gates, and stop with a structured work summary that ends by asking whether
   to commit and push. **No commit or push happens without your explicit go-ahead.**
5. After the commit lands on `dev` and CI is green, open a PR `dev` -> `main`. After merge, resync
   `dev` (`git fetch origin && git checkout dev && git merge origin/main && git push origin dev`)
   before starting the next prompt.
6. If a session reports drift between the plan and current code, stop and reconcile (edit the plan
   or adjust the prompt) before continuing. The plan is the floor, not a free-floating wish list.

The prompts are ordered to match the remediation plan's commit numbering (Section 8 checklist). Read
`remediation_plan.md` Section 7 (Dependency Analysis) once before starting; the prereqs in each
prompt below encode it but the picture is easier to hold from the DAG.

---

## Group A -- Foundations

### Commit 1 -- `feat(utils): add money.round_money boundary helper (E-26)`

**Prereqs on dev:** none. **Closes:** HIGH-04 (E-26 foundation; no call-site swaps yet).

```text
You are executing Commit 1 of the Shekel financial-calculation audit remediation in a fresh
session. Work in the project root on the dev branch.

Required reading -- read each in full BEFORE anything else (use @path so they are fetched,
do not summarize from memory or training):
- @docs/audits/financial_calculations/remediation_plan.md (Sections 0-7 for context; Section
  9 "Commit 1" for this commit's full A-H specification)
- @CLAUDE.md
- @docs/coding-standards.md
- @docs/testing-standards.md
- @docs/audits/financial_calculations/08_findings.md (focus: HIGH-04)

Objective: introduce app/utils/money.py with round_money (2dp, ROUND_HALF_UP) and
round_money_ceiling (2dp, ROUND_CEILING). This commit introduces the helper only; it does
not swap any call sites (those are domain commits later in the plan). Reject float input
with TypeError so a caller cannot bypass the Decimal contract.

Production files this commit touches (re-confirm by grep before editing):
- app/utils/money.py (new)
- tests/test_utils/test_money.py (new)

Apply these rules (the plan's Section 1 is the authoritative version):
1. The plan's specification for this commit (Section 9, subsections A through H) is the
   floor, not the ceiling. If verification surfaces extra in-scope refinements, fold them
   in and explain in the work summary.
2. Trust-but-verify: re-grep every cited symbol; read every file you will change in full;
   confirm the audit/plan claim still holds against current code before editing. If reality
   has drifted, stop and report in the work summary before continuing.
3. No shortcuts, no band-aid fixes. Fix root causes. Decimal money from strings, IDs and
   semantic booleans for business logic (never name strings), DRY/SOLID, fully normalized
   schema, pythonic type-hinted code, specific exceptions, substantive docstrings, no
   Unicode em/en dashes (use -- or -).
4. Never modify a test to make it pass except for the documented exception in plan Section 1
   rule 2: tests pinning a shipping wrong number this finding proves wrong. Every re-pinned
   assertion gets a comment naming the finding ID and the hand-computed arithmetic. List
   each in the work summary.
5. Targeted pytest during edits; pylint app/ --fail-on=E,F clean (no new warnings vs
   baseline); full pytest (-n 12 default) green as the per-commit final gate. Migrations
   (if any) round-trip upgrade->downgrade->upgrade cleanly; destructive changes get a Review
   docstring line and explicit developer approval before authoring.
6. Stay in scope. Out-of-scope issues, gold-plating opportunities, or refactors you noticed but did not
   perform MUST be flagged in the work summary with file:line and a one-sentence reason for not acting.
   Do not silently fold them in. Items that are not directly handled by a future commit need to be
   added to docs/audits/financial_calculations/remediation_follow_up.md. Any trivial items offer to
   address as a separate commit.
7. Do not push. After the work is green, present the work summary and ASK whether to commit
   and push to dev (this triggers CI; PR-to-main is required for promotion).

Work summary format (use these labels verbatim):
A. Verification: claims you re-verified; any drift found.
B. Files changed: list with one-line purpose each.
C. Tests added/modified: ID, name, hand-computed Decimal + arithmetic comment.
D. Re-pinned tests: finding ID + arithmetic per assertion, or "none".
E. Targeted-suite and full-suite final summary lines, verbatim from pytest.
F. Pylint final line, verbatim.
G. Migrations: upgrade->downgrade->upgrade result, or "n/a".
H. Invariants: which existing regression locks stayed green; which new locks added.
I. Discovered refinements folded in: each with rationale.
J. OUT OF SCOPE -- flagged, not fixed: file:line + reason per item + F-# entry in remediation_follow_up.md.
K. Open questions / assumptions made.
L. Proposed commit message (format: <type>(<scope>): <what> with the Co-Authored-By trailer
   per CLAUDE.md).
M. Ask: "Ready to commit and push to dev?"

If anything is unclear, ASK. Do not guess about financial logic, what a function returns,
or what a table contains.
```

---

### Commit 2 -- `refactor(status): centralize balance-contributing status predicate (E-15)`

**Prereqs on dev:** none. **Closes:** MED-02 / D6-09 (E-15 foundation; no consumer rewired yet).

```text
You are executing Commit 2 of the Shekel financial-calculation audit remediation in a fresh
session. Work in the project root on the dev branch.

Required reading -- read each in full BEFORE anything else:
- @docs/audits/financial_calculations/remediation_plan.md (Sections 0-7; Section 9 "Commit
  2" for this commit's A-H specification)
- @CLAUDE.md, @docs/coding-standards.md, @docs/testing-standards.md
- @docs/audits/financial_calculations/08_findings.md (focus: MED-02)
- @app/models/transaction.py (the effective_amount property and Status relationship)
- @app/models/status.py if present (Status.is_settled, .excludes_from_balance, .is_immutable)
- @app/ref_cache.py and @app/enums.py (cached ID access, the only correct way to look up
  status IDs)

Objective: introduce one semantic predicate module (e.g. app/utils/balance_predicates.py)
exposing is_balance_contributing(txn), is_projected(txn), balance_excluded_status_ids(),
and a SQLAlchemy clause builder balance_contributing_clause() so the Python predicate and
the ORM filter share one definition. Use Status boolean columns (excludes_from_balance,
is_settled) and cached IDs; never compare against name strings. No consumer is rewired in
this commit -- Commits 5/10/29 do that as they touch each call site.

Production files this commit touches (re-confirm by grep before editing):
- app/utils/balance_predicates.py (new)
- tests/test_utils/test_balance_predicates.py (new)

Apply these rules (the plan's Section 1 is the authoritative version):
1. The plan's specification for this commit (Section 9, subsections A through H) is the
   floor, not the ceiling. If verification surfaces extra in-scope refinements, fold them
   in and explain in the work summary.
2. Trust-but-verify: re-grep every cited symbol; read every file you will change in full;
   confirm the audit/plan claim still holds against current code before editing. If reality
   has drifted, stop and report in the work summary before continuing.
3. No shortcuts, no band-aid fixes. Fix root causes. Decimal money from strings, IDs and
   semantic booleans for business logic (never name strings), DRY/SOLID, fully normalized
   schema, pythonic type-hinted code, specific exceptions, substantive docstrings, no
   Unicode em/en dashes (use -- or -).
4. Never modify a test to make it pass except for the documented exception in plan Section 1
   rule 2: tests pinning a shipping wrong number this finding proves wrong. Every re-pinned
   assertion gets a comment naming the finding ID and the hand-computed arithmetic. List
   each in the work summary.
5. Targeted pytest during edits; pylint app/ --fail-on=E,F clean (no new warnings vs
   baseline); full pytest (-n 12 default) green as the per-commit final gate. Migrations
   (if any) round-trip upgrade->downgrade->upgrade cleanly; destructive changes get a Review
   docstring line and explicit developer approval before authoring.
6. Stay in scope. Out-of-scope issues, gold-plating opportunities, or refactors you noticed but did not
   perform MUST be flagged in the work summary with file:line and a one-sentence reason for not acting.
   Do not silently fold them in. Items that are not directly handled by a future commit need to be
   added to docs/audits/financial_calculations/remediation_follow_up.md. Any trivial items offer to
   address as a separate commit.
7. Do not push. After the work is green, present the work summary and ASK whether to commit
   and push to dev (this triggers CI; PR-to-main is required for promotion).

Work summary format (use these labels verbatim):
A. Verification: claims you re-verified; any drift found.
B. Files changed: list with one-line purpose each.
C. Tests added/modified: ID, name, hand-computed Decimal + arithmetic comment.
D. Re-pinned tests: finding ID + arithmetic per assertion, or "none".
E. Targeted-suite and full-suite final summary lines, verbatim from pytest.
F. Pylint final line, verbatim.
G. Migrations: upgrade->downgrade->upgrade result, or "n/a".
H. Invariants: which existing regression locks stayed green; which new locks added.
I. Discovered refinements folded in: each with rationale.
J. OUT OF SCOPE -- flagged, not fixed: file:line + reason per item + F-# entry in remediation_follow_up.md.
K. Open questions / assumptions made.
L. Proposed commit message (format: <type>(<scope>): <what> with the Co-Authored-By trailer
   per CLAUDE.md).
M. Ask: "Ready to commit and push to dev?"

Specific verification gates for this commit:
- After your changes, grep proves the predicate uses IDs/booleans, never name strings:
  `grep -n "\.name ==\|Status.name" app/utils/balance_predicates.py` must return empty.
- The Python predicate and the SQL clause are tested for parity on a realistic seeded mix
  (the same set of transactions must be classified identically in-Python and via the ORM
  clause). This is the load-bearing test; do not skip it.

If anything is unclear, ASK. Do not guess at Status semantics; read ref_seeds.py for the
canonical is_settled / is_immutable / excludes_from_balance flag values per status.
```

---

## Group B -- Balance source of truth (CRIT-01 family + HIGH-01/HIGH-02)

### Commit 3 -- `fix(anchor): backfill origination anchor so anchor period is never NULL (E-19)`

**Prereqs on dev:** none (Commit 4 depends on this). **Closes:** CRIT-01 (E-19 part 1; eliminates
the NULL-anchor fork).

```text
You are executing Commit 3 of the Shekel financial-calculation audit remediation in a fresh
session. Work in the project root on the dev branch.

Required reading -- read each in full BEFORE anything else:
- @docs/audits/financial_calculations/remediation_plan.md (Sections 0-7; Section 9 "Commit
  3" for the A-H specification)
- @CLAUDE.md, @docs/coding-standards.md, @docs/testing-standards.md
- @docs/audits/financial_calculations/08_findings.md (focus: CRIT-01)
- @docs/audits/financial_calculations/05_symptoms.md (focus: symptoms #1 and #5 reproduction
  paths; the anchor-NULL fork is one of the divergence axes)
- @app/models/account.py (Account, AccountAnchorHistory -- already exists for checking
  true-ups; this commit reuses the same pattern)
- @app/models/pay_period.py
- @app/routes/accounts.py and @app/services/auth_service.py (account-creation paths)

Objective: make Account.current_anchor_balance and Account.current_anchor_period_id NOT
NULL by (1) backfilling every existing account with an origination anchor and a matching
AccountAnchorHistory row, (2) verifying zero NULLs remain with a diagnostic SELECT, (3)
altering both columns to NOT NULL with a named CHECK on balance presence. Every account-
creation path always writes an anchor and a history row at creation. After this commit,
the NULL-anchor fork in the five balance producers is dead code (Commits 5-8 delete it).

Production files this commit touches (re-confirm by grep before editing; audit/plan line
numbers may have drifted):
- app/models/account.py
- app/routes/accounts.py (account-creation route)
- app/services/auth_service.py (signup path that creates the default account)
- migrations/versions/<auto>_backfill_account_anchor.py (new)
- tests/test_models/test_account_anchor.py (new or extend existing)

Apply these rules (the plan's Section 1 is the authoritative version):
1. The plan's specification for this commit (Section 9, subsections A through H) is the
   floor, not the ceiling. If verification surfaces extra in-scope refinements, fold them
   in and explain in the work summary.
2. Trust-but-verify: re-grep every cited symbol; read every file you will change in full;
   confirm the audit/plan claim still holds against current code before editing. If reality
   has drifted, stop and report in the work summary before continuing.
3. No shortcuts, no band-aid fixes. Fix root causes. Decimal money from strings, IDs and
   semantic booleans for business logic (never name strings), DRY/SOLID, fully normalized
   schema, pythonic type-hinted code, specific exceptions, substantive docstrings, no
   Unicode em/en dashes (use -- or -).
4. Never modify a test to make it pass except for the documented exception in plan Section 1
   rule 2: tests pinning a shipping wrong number this finding proves wrong. Every re-pinned
   assertion gets a comment naming the finding ID and the hand-computed arithmetic. List
   each in the work summary.
5. Targeted pytest during edits; pylint app/ --fail-on=E,F clean (no new warnings vs
   baseline); full pytest (-n 12 default) green as the per-commit final gate. Migrations
   (if any) round-trip upgrade->downgrade->upgrade cleanly; destructive changes get a Review
   docstring line and explicit developer approval before authoring.
6. Stay in scope. Out-of-scope issues, gold-plating opportunities, or refactors you noticed but did not
   perform MUST be flagged in the work summary with file:line and a one-sentence reason for not acting.
   Do not silently fold them in. Items that are not directly handled by a future commit need to be
   added to docs/audits/financial_calculations/remediation_follow_up.md. Any trivial items offer to
   address as a separate commit.
7. Do not push. After the work is green, present the work summary and ASK whether to commit
   and push to dev (this triggers CI; PR-to-main is required for promotion).

Migration constraints (these are non-negotiable; see @docs/coding-standards.md "Migrations"
and @CLAUDE.md Definition of Done):
- Three-step populated-table pattern: (a) add_column nullable (already nullable here, so
  effectively skip) -> update backfill with a deterministic derivation -> verify zero
  NULLs with a SELECT count, raise RuntimeError with the diagnostic SQL embedded if any
  survive -> alter_column to nullable=False. Each step explained in the migration's
  module-level docstring.
- Type/constraint change requires a Review: docstring line per the coding standard.
- Downgrade must re-widen the columns to nullable; data is retained (reversible).
- Test both directions: `flask db upgrade` then `flask db downgrade` then `flask db
  upgrade` cleanly; assert no orphan rows.
- After migration changes the schema, `python scripts/build_test_template.py` (the test
  template must be rebuilt; entrypoint trigger-count is unaffected by this commit but
  Commit 12 will change it).

Derivation rule for the backfill (document in migration docstring): anchor_period = the
PayPeriod containing the account's earliest non-deleted transaction's pay_period (else the
earliest period for the user); anchor_balance = COALESCE(existing column, 0.00). 0.00 is
a real Decimal zero (E-12; never treated as "missing").

Work summary format (use these labels verbatim):
A. Verification: claims you re-verified; any drift found.
B. Files changed: list with one-line purpose each.
C. Tests added/modified: ID, name, hand-computed Decimal + arithmetic comment.
D. Re-pinned tests: finding ID + arithmetic per assertion, or "none".
E. Targeted-suite and full-suite final summary lines, verbatim from pytest.
F. Pylint final line, verbatim.
G. Migrations: upgrade->downgrade->upgrade result, or "n/a".
H. Invariants: which existing regression locks stayed green; which new locks added.
I. Discovered refinements folded in: each with rationale.
J. OUT OF SCOPE -- flagged, not fixed: file:line + reason per item + F-# entry in remediation_follow_up.md.
K. Open questions / assumptions made.
L. Proposed commit message (format: <type>(<scope>): <what> with the Co-Authored-By trailer
   per CLAUDE.md).
M. Ask: "Ready to commit and push to dev?"

Specific verification gates for this commit:
- Post-upgrade: `SELECT count(*) FROM budget.accounts WHERE current_anchor_period_id IS
  NULL OR current_anchor_balance IS NULL` is 0.
- A test asserts an account created via the route immediately has both columns non-NULL
  and an AccountAnchorHistory row.
- A model-level test asserts attempting to flush an Account with NULL anchor fails with
  IntegrityError.

If anything is unclear, ASK. Do not guess about the existing AccountAnchorHistory
semantics; read its definition and its unique-index strategy in full.
```

---

### Commit 4 -- `feat(balance): date-anchored anchor resolver, NULL state unreachable (E-19)`

**Prereqs on dev:** 3. **Closes:** CRIT-01 (E-19 part 2; one anchor resolver consumed by Commit 5).

```text
You are executing Commit 4 of the Shekel financial-calculation audit remediation in a fresh
session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/audits/financial_calculations/remediation_plan.md (Sections 0-7; Section 9 "Commit
  4" for the A-H specification)
- @CLAUDE.md, @docs/coding-standards.md, @docs/testing-standards.md
- @docs/audits/financial_calculations/08_findings.md (CRIT-01)
- @app/models/account.py (Account + AccountAnchorHistory; the latter is now guaranteed to
  have at least one row per account because of Commit 3)
- @app/services/balance_calculator.py (only to confirm what currently consumes the anchor;
  do not change it in this commit)

Objective: introduce app/services/balance_resolver.py with resolve_anchor(account,
scenario_id) -> AnchorPoint, where AnchorPoint is a frozen dataclass (balance: Decimal,
period: PayPeriod, as_of_date: date). Read the latest AccountAnchorHistory row as the
dated source of truth; treat Account.current_anchor_* columns as a denormalized cache of
that latest row and reconcile (log via log_event if they disagree -- the history row
wins). Never return None (Commit 3 guarantees at least one history row). The Decimal
balance is constructed via Decimal(str(...)). Services boundary: no flask imports.

Production files this commit touches:
- app/services/balance_resolver.py (new; this commit creates the anchor section only;
  Commit 5 extends with the balance producer)
- tests/test_services/test_balance_resolver_anchor.py (new)

Apply these rules (the plan's Section 1 is the authoritative version):
1. The plan's specification for this commit (Section 9, subsections A through H) is the
   floor, not the ceiling. If verification surfaces extra in-scope refinements, fold them
   in and explain in the work summary.
2. Trust-but-verify: re-grep every cited symbol; read every file you will change in full;
   confirm the audit/plan claim still holds against current code before editing. If reality
   has drifted, stop and report in the work summary before continuing.
3. No shortcuts, no band-aid fixes. Fix root causes. Decimal money from strings, IDs and
   semantic booleans for business logic (never name strings), DRY/SOLID, fully normalized
   schema, pythonic type-hinted code, specific exceptions, substantive docstrings, no
   Unicode em/en dashes (use -- or -).
4. Never modify a test to make it pass except for the documented exception in plan Section 1
   rule 2: tests pinning a shipping wrong number this finding proves wrong. Every re-pinned
   assertion gets a comment naming the finding ID and the hand-computed arithmetic. List
   each in the work summary.
5. Targeted pytest during edits; pylint app/ --fail-on=E,F clean (no new warnings vs
   baseline); full pytest (-n 12 default) green as the per-commit final gate. Migrations
   (if any) round-trip upgrade->downgrade->upgrade cleanly; destructive changes get a Review
   docstring line and explicit developer approval before authoring.
6. Stay in scope. Out-of-scope issues, gold-plating opportunities, or refactors you noticed but did not
   perform MUST be flagged in the work summary with file:line and a one-sentence reason for not acting.
   Do not silently fold them in. Items that are not directly handled by a future commit need to be
   added to docs/audits/financial_calculations/remediation_follow_up.md. Any trivial items offer to
   address as a separate commit.
7. Do not push. After the work is green, present the work summary and ASK whether to commit
   and push to dev (this triggers CI; PR-to-main is required for promotion).

Services-boundary gate (B6-01 / CLAUDE.md architecture):
- `grep -nE '^(from|import)\s+flask\b|\b(request|session|current_app|render_template)\b'
  app/services/balance_resolver.py` must return empty after your work.

Work summary format (use these labels verbatim):
A. Verification: claims you re-verified; any drift found.
B. Files changed: list with one-line purpose each.
C. Tests added/modified: ID, name, hand-computed Decimal + arithmetic comment.
D. Re-pinned tests: finding ID + arithmetic per assertion, or "none".
E. Targeted-suite and full-suite final summary lines, verbatim from pytest.
F. Pylint final line, verbatim.
G. Migrations: upgrade->downgrade->upgrade result, or "n/a".
H. Invariants: which existing regression locks stayed green; which new locks added.
I. Discovered refinements folded in: each with rationale.
J. OUT OF SCOPE -- flagged, not fixed: file:line + reason per item + F-# entry in remediation_follow_up.md.
K. Open questions / assumptions made.
L. Proposed commit message (format: <type>(<scope>): <what> with the Co-Authored-By trailer
   per CLAUDE.md).
M. Ask: "Ready to commit and push to dev?"

Specific verification gates for this commit:
- A scenario-scoped test proves resolve_anchor returns the scenario's anchor, not a
  sibling scenario's.
- A test where Account.current_anchor_balance is engineered to disagree with the latest
  history row asserts the history row wins.
- A test asserts a zero-balance anchor is honored as Decimal("0.00"), not coerced to a
  default (E-12: zero is a value).

If anything is unclear, ASK.
```

---

### Commit 5 -- `feat(balance): canonical entries-aware balance/subtotal producer (E-25)`

**Prereqs on dev:** 1, 2, 4. **Closes:** CRIT-01 (E-25; removes the silent-degrade seam; routes grid

- dashboard_service).

```text
You are executing Commit 5 of the Shekel financial-calculation audit remediation in a fresh
session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/audits/financial_calculations/remediation_plan.md (Sections 0-7; Section 9 "Commit
  5" A-H)
- @CLAUDE.md, @docs/coding-standards.md, @docs/testing-standards.md
- @docs/audits/financial_calculations/08_findings.md (CRIT-01)
- @docs/audits/financial_calculations/05_symptoms.md (symptom #1 hypothesis tree and the
  worked $160 vs $114.29 example)
- @app/services/balance_calculator.py (the engine that is being routed THROUGH; do not
  rewrite its math -- CLAUDE.md rule 10. You DELETE the silent-degrade short-circuit
  inside _entry_aware_amount because the new producer guarantees entries are loaded.)
- @app/models/transaction.py (effective_amount property, entries relationship -- lazy
  select by default; that default is the seam)
- @app/models/transaction_entry.py (is_cleared, is_credit, amount)
- @app/routes/grid.py (consumer being routed; it already eager-loads entries -- numbers
  do not change for it)
- @app/services/dashboard_service.py (consumer being routed; also already eager-loads)
- @app/utils/money.py (Commit 1; use round_money at the boundary)
- @app/utils/balance_predicates.py (Commit 2; sole status gate)

Objective: extend balance_resolver.py with balances_for(account, scenario_id, periods)
-> BalanceResult and period_subtotal(...). The producer owns the query and always
selectinload(Transaction.entries) and selectinload(Transaction.status); calls
resolve_anchor (Commit 4); uses is_balance_contributing (Commit 2) as the only status
gate; uses round_money (Commit 1) as the only rounding boundary. Reuse the pure math in
balance_calculator (the carry-forward, the income/expense partitioning) -- do not rewrite
it. The unconditional entries-aware reduction max(estimated - cleared_debit - sum_credit,
uncleared_debit) replaces the silent _entry_aware_amount short-circuit. Route grid.py
and dashboard_service.py through balances_for; their pinned tests must remain unchanged
(those callers already loaded entries, so their numbers do not change).

Production files this commit touches:
- app/services/balance_resolver.py (extension)
- app/services/balance_calculator.py (remove the `'entries' not in txn.__dict__` short-
  circuit and document why -- the new producer makes the fallback unreachable for live
  callers; the math functions stay)
- app/routes/grid.py (replace its query+calculate_balances call with balances_for)
- app/services/dashboard_service.py (same)
- tests/test_services/test_balance_resolver.py (new)

Apply these rules (the plan's Section 1 is the authoritative version):
1. The plan's specification for this commit (Section 9, subsections A through H) is the
   floor, not the ceiling. If verification surfaces extra in-scope refinements, fold them
   in and explain in the work summary.
2. Trust-but-verify: re-grep every cited symbol; read every file you will change in full;
   confirm the audit/plan claim still holds against current code before editing. If reality
   has drifted, stop and report in the work summary before continuing.
3. No shortcuts, no band-aid fixes. Fix root causes. Decimal money from strings, IDs and
   semantic booleans for business logic (never name strings), DRY/SOLID, fully normalized
   schema, pythonic type-hinted code, specific exceptions, substantive docstrings, no
   Unicode em/en dashes (use -- or -).
4. Never modify a test to make it pass except for the documented exception in plan Section 1
   rule 2: tests pinning a shipping wrong number this finding proves wrong. Every re-pinned
   assertion gets a comment naming the finding ID and the hand-computed arithmetic. List
   each in the work summary.
5. Targeted pytest during edits; pylint app/ --fail-on=E,F clean (no new warnings vs
   baseline); full pytest (-n 12 default) green as the per-commit final gate. Migrations
   (if any) round-trip upgrade->downgrade->upgrade cleanly; destructive changes get a Review
   docstring line and explicit developer approval before authoring.
6. Stay in scope. Out-of-scope issues, gold-plating opportunities, or refactors you noticed but did not
   perform MUST be flagged in the work summary with file:line and a one-sentence reason for not acting.
   Do not silently fold them in. Items that are not directly handled by a future commit need to be
   added to docs/audits/financial_calculations/remediation_follow_up.md. Any trivial items offer to
   address as a separate commit.
7. Do not push. After the work is green, present the work summary and ASK whether to commit
   and push to dev (this triggers CI; PR-to-main is required for promotion).

Note for this commit (rule 1 emphasis): this commit may discover additional in-scope
consumer sites the audit under-counted (refinement R-1 in the plan lists investment.py x2
and retirement_dashboard_service.py); do not route those here (they are Commit 8) but flag
in the work summary that Commit 8 will pick them up.

Services-boundary gate: balance_resolver still has zero Flask imports.

Work summary format (use these labels verbatim):
A. Verification: claims you re-verified; any drift found.
B. Files changed: list with one-line purpose each.
C. Tests added/modified: ID, name, hand-computed Decimal + arithmetic comment.
D. Re-pinned tests: finding ID + arithmetic per assertion, or "none".
E. Targeted-suite and full-suite final summary lines, verbatim from pytest.
F. Pylint final line, verbatim.
G. Migrations: upgrade->downgrade->upgrade result, or "n/a".
H. Invariants: which existing regression locks stayed green; which new locks added.
I. Discovered refinements folded in: each with rationale.
J. OUT OF SCOPE -- flagged, not fixed: file:line + reason per item + F-# entry in remediation_follow_up.md.
K. Open questions / assumptions made.
L. Proposed commit message (format: <type>(<scope>): <what> with the Co-Authored-By trailer
   per CLAUDE.md).
M. Ask: "Ready to commit and push to dev?"

Specific verification gates for this commit:
- After your changes, `grep -n "not in txn.__dict__\|not in.*entries" app/services/
  balance_resolver.py app/services/balance_calculator.py` must return empty.
- A test exercises balances_for WITHOUT the caller pre-loading entries and asserts the
  same Decimal as a test that DOES pre-load. This is the core fix; if these disagree
  the seam is not actually removed.
- Grid and dashboard pinned-value tests stay green BYTE-IDENTICAL (assert-unchanged
  category). If any value changes, stop and report.

Re-pinned tests: none expected (grid + dashboard already on the correct path).

If anything is unclear, ASK. Do not guess at the carry-forward math; if you need to
understand the engine's period-by-period running balance, read balance_calculator end
to end and explain it (to yourself) before changing anything.
```

---

### Commit 6 -- `fix(savings): route /savings balances through canonical producer`

**Prereqs on dev:** 5. **Closes:** CRIT-01 / F-009 / symptom #1 ($160 vs $114.29).

```text
You are executing Commit 6 of the Shekel financial-calculation audit remediation in a fresh
session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/audits/financial_calculations/remediation_plan.md (Sections 0-7; Section 9 "Commit
  6" A-H)
- @CLAUDE.md, @docs/coding-standards.md, @docs/testing-standards.md
- @docs/audits/financial_calculations/08_findings.md (CRIT-01)
- @docs/audits/financial_calculations/05_symptoms.md (symptom #1 worked example with the
  hand-computed Decimal arithmetic; you will re-pin to those values)
- @app/services/savings_dashboard_service.py (the file being routed)
- @app/services/balance_resolver.py (the producer from Commit 5)
- @tests/test_services/test_savings_dashboard_service.py (the tests being re-pinned)

Objective: replace savings_dashboard_service's manual transaction query and calculate_
balances/calculate_balances_with_interest calls with balance_resolver.balances_for. The
per-account dispatch shape stays (MED-01 / Commit 28 collapses the dual dispatcher
later). Re-pin assertions that previously expected the pre-fix value (typically $114.29
for the symptom tuple) to the correct entries-aware value ($160.00), each with a comment
naming finding F-009 / CRIT-01 and the arithmetic (anchor - max(estimated - cleared_debit
- sum_credit, uncleared_debit) = 614.29 - max(500 - 45.71 - 0, 0) = 160.00).

Production files this commit touches:
- app/services/savings_dashboard_service.py
- tests/test_services/test_savings_dashboard_service.py (re-pins with arithmetic comments)

Apply these rules (the plan's Section 1 is the authoritative version):
1. The plan's specification for this commit (Section 9, subsections A through H) is the
   floor, not the ceiling. If verification surfaces extra in-scope refinements, fold them
   in and explain in the work summary.
2. Trust-but-verify: re-grep every cited symbol; read every file you will change in full;
   confirm the audit/plan claim still holds against current code before editing. If reality
   has drifted, stop and report in the work summary before continuing.
3. No shortcuts, no band-aid fixes. Fix root causes. Decimal money from strings, IDs and
   semantic booleans for business logic (never name strings), DRY/SOLID, fully normalized
   schema, pythonic type-hinted code, specific exceptions, substantive docstrings, no
   Unicode em/en dashes (use -- or -).
4. Never modify a test to make it pass except for the documented exception in plan Section 1
   rule 2: tests pinning a shipping wrong number this finding proves wrong. Every re-pinned
   assertion gets a comment naming the finding ID and the hand-computed arithmetic. List
   each in the work summary.
5. Targeted pytest during edits; pylint app/ --fail-on=E,F clean (no new warnings vs
   baseline); full pytest (-n 12 default) green as the per-commit final gate. Migrations
   (if any) round-trip upgrade->downgrade->upgrade cleanly; destructive changes get a Review
   docstring line and explicit developer approval before authoring.
6. Stay in scope. Out-of-scope issues, gold-plating opportunities, or refactors you noticed but did not
   perform MUST be flagged in the work summary with file:line and a one-sentence reason for not acting.
   Do not silently fold them in. Items that are not directly handled by a future commit need to be
   added to docs/audits/financial_calculations/remediation_follow_up.md. Any trivial items offer to
   address as a separate commit.
7. Do not push. After the work is green, present the work summary and ASK whether to commit
   and push to dev (this triggers CI; PR-to-main is required for promotion).

Re-pinned tests: list every assertion you re-pin, with the finding ID (F-009 or CRIT-01)
and the arithmetic in the test comment, then in the work summary. Do not silently change
any other test.

Work summary format (use these labels verbatim):
A. Verification: claims you re-verified; any drift found.
B. Files changed: list with one-line purpose each.
C. Tests added/modified: ID, name, hand-computed Decimal + arithmetic comment.
D. Re-pinned tests: finding ID + arithmetic per assertion, or "none".
E. Targeted-suite and full-suite final summary lines, verbatim from pytest.
F. Pylint final line, verbatim.
G. Migrations: upgrade->downgrade->upgrade result, or "n/a".
H. Invariants: which existing regression locks stayed green; which new locks added.
I. Discovered refinements folded in: each with rationale.
J. OUT OF SCOPE -- flagged, not fixed: file:line + reason per item + F-# entry in remediation_follow_up.md.
K. Open questions / assumptions made.
L. Proposed commit message (format: <type>(<scope>): <what> with the Co-Authored-By trailer
   per CLAUDE.md).
M. Ask: "Ready to commit and push to dev?"

Specific verification gates for this commit:
- A new test renders /savings on the symptom #1 tuple and asserts the checking tile equals
  the grid's current-period balance Decimal exactly.
- HYSA path: any account with cleared entries gets the entry-aware reduction; an account
  with NO entries returns the same number as before this commit (assert-unchanged).

If anything is unclear, ASK. Do not invent the "correct" pre-fix value for a test you are
re-pinning; if you cannot compute it by hand from the inputs and explain it, stop and ask.
```

---

### Commit 7 -- `fix(accounts): route /accounts checking detail through canonical producer`

**Prereqs on dev:** 5 (and effectively 3 because the anchor-NULL fork goes away). **Closes:**
CRIT-01 / F-001 / symptom #5 checking facet.

```text
You are executing Commit 7 of the Shekel financial-calculation audit remediation in a fresh
session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/audits/financial_calculations/remediation_plan.md (Sections 0-7; Section 9 "Commit
  7" A-H)
- @CLAUDE.md, @docs/coding-standards.md, @docs/testing-standards.md
- @docs/audits/financial_calculations/08_findings.md (CRIT-01)
- @docs/audits/financial_calculations/05_symptoms.md (symptom #5 hypothesis tree)
- @app/routes/accounts.py (the file being routed; re-grep for calculate_balances callers)
- @app/services/balance_resolver.py (the producer from Commit 5)
- @tests/test_routes/test_accounts.py

Objective: replace the /accounts checking-detail query and calculate_balances_with_interest
/ calculate_balances calls with balance_resolver.balances_for. With Commit 3 in place, the
anchor-NULL fallback fork in this path is dead code -- DELETE it (do not leave unreachable
branches; CLAUDE.md rule 5 "do it right"). Re-pin checking-detail balance assertions that
expected the pre-fix value, with finding F-001 / CRIT-01 and the arithmetic.

Production files this commit touches:
- app/routes/accounts.py
- tests/test_routes/test_accounts.py (re-pins + new equality test)

Apply these rules (the plan's Section 1 is the authoritative version):
1. The plan's specification for this commit (Section 9, subsections A through H) is the
   floor, not the ceiling. If verification surfaces extra in-scope refinements, fold them
   in and explain in the work summary.
2. Trust-but-verify: re-grep every cited symbol; read every file you will change in full;
   confirm the audit/plan claim still holds against current code before editing. If reality
   has drifted, stop and report in the work summary before continuing.
3. No shortcuts, no band-aid fixes. Fix root causes. Decimal money from strings, IDs and
   semantic booleans for business logic (never name strings), DRY/SOLID, fully normalized
   schema, pythonic type-hinted code, specific exceptions, substantive docstrings, no
   Unicode em/en dashes (use -- or -).
4. Never modify a test to make it pass except for the documented exception in plan Section 1
   rule 2: tests pinning a shipping wrong number this finding proves wrong. Every re-pinned
   assertion gets a comment naming the finding ID and the hand-computed arithmetic. List
   each in the work summary.
5. Targeted pytest during edits; pylint app/ --fail-on=E,F clean (no new warnings vs
   baseline); full pytest (-n 12 default) green as the per-commit final gate. Migrations
   (if any) round-trip upgrade->downgrade->upgrade cleanly; destructive changes get a Review
   docstring line and explicit developer approval before authoring.
6. Stay in scope. Out-of-scope issues, gold-plating opportunities, or refactors you noticed but did not
   perform MUST be flagged in the work summary with file:line and a one-sentence reason for not acting.
   Do not silently fold them in. Items that are not directly handled by a future commit need to be
   added to docs/audits/financial_calculations/remediation_follow_up.md. Any trivial items offer to
   address as a separate commit.
7. Do not push. After the work is green, present the work summary and ASK whether to commit
   and push to dev (this triggers CI; PR-to-main is required for promotion).

Work summary format (use these labels verbatim):
A. Verification: claims you re-verified; any drift found.
B. Files changed: list with one-line purpose each.
C. Tests added/modified: ID, name, hand-computed Decimal + arithmetic comment.
D. Re-pinned tests: finding ID + arithmetic per assertion, or "none".
E. Targeted-suite and full-suite final summary lines, verbatim from pytest.
F. Pylint final line, verbatim.
G. Migrations: upgrade->downgrade->upgrade result, or "n/a".
H. Invariants: which existing regression locks stayed green; which new locks added.
I. Discovered refinements folded in: each with rationale.
J. OUT OF SCOPE -- flagged, not fixed: file:line + reason per item + F-# entry in remediation_follow_up.md.
K. Open questions / assumptions made.
L. Proposed commit message (format: <type>(<scope>): <what> with the Co-Authored-By trailer
   per CLAUDE.md).
M. Ask: "Ready to commit and push to dev?"

Specific verification gates for this commit:
- A new test renders /accounts checking detail and the grid for the symptom tuple and
  asserts identical Decimal.
- A new test creates an account with anchor balance 0.00 (a real zero) and asserts
  /accounts shows a populated zero-anchored projection -- not blank, not omitted.
- `grep -n "or current_period\|if account\.current_anchor_period_id is None" app/routes/
  accounts.py` returns empty (dead fork removed).

If anything is unclear, ASK.
```

---

### Commit 8 -- `fix(balance): route year-end/net-worth/investment/retirement through producer`

**Prereqs on dev:** 5. **Closes:** CRIT-01 + plan refinement R-1 (the two investment sites and the
retirement site the audit did not enumerate).

```text
You are executing Commit 8 of the Shekel financial-calculation audit remediation in a fresh
session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/audits/financial_calculations/remediation_plan.md (Sections 0-7; Section 3 R-1 for
  the extra sites; Section 9 "Commit 8" A-H)
- @CLAUDE.md, @docs/coding-standards.md, @docs/testing-standards.md
- @docs/audits/financial_calculations/08_findings.md (CRIT-01)
- @app/services/year_end_summary_service.py (net-worth + balance map; the LOAN net-worth
  path is NOT this commit -- Commit 15 handles loan-side routing through loan_resolver;
  this commit routes only the checking-style reads in year-end)
- @app/routes/investment.py (two query sites without selectinload(entries))
- @app/services/retirement_dashboard_service.py (one query site without selectinload)
- @app/services/balance_resolver.py
- relevant test files in tests/test_services/ and tests/test_routes/

Objective: route the remaining live external callers of calculate_balances* (for checking-
style balances) through balance_resolver.balances_for. After this commit, the silent-
degrade seam has no live external caller. The loan/net-worth schedule path is unchanged
here -- Commit 15 routes it through loan_resolver. Re-pin any tests that asserted the
pre-fix entries-unaware value with finding R-1 / CRIT-01 and arithmetic.

Production files this commit touches (re-grep all line numbers; the audit's are stale):
- app/services/year_end_summary_service.py
- app/routes/investment.py
- app/services/retirement_dashboard_service.py
- tests/test_services/test_year_end_summary_service.py
- tests/test_routes/test_investment.py
- tests/test_services/test_retirement_dashboard_service.py

Apply these rules (the plan's Section 1 is the authoritative version):
1. The plan's specification for this commit (Section 9, subsections A through H) is the
   floor, not the ceiling. If verification surfaces extra in-scope refinements, fold them
   in and explain in the work summary.
2. Trust-but-verify: re-grep every cited symbol; read every file you will change in full;
   confirm the audit/plan claim still holds against current code before editing. If reality
   has drifted, stop and report in the work summary before continuing.
3. No shortcuts, no band-aid fixes. Fix root causes. Decimal money from strings, IDs and
   semantic booleans for business logic (never name strings), DRY/SOLID, fully normalized
   schema, pythonic type-hinted code, specific exceptions, substantive docstrings, no
   Unicode em/en dashes (use -- or -).
4. Never modify a test to make it pass except for the documented exception in plan Section 1
   rule 2: tests pinning a shipping wrong number this finding proves wrong. Every re-pinned
   assertion gets a comment naming the finding ID and the hand-computed arithmetic. List
   each in the work summary.
5. Targeted pytest during edits; pylint app/ --fail-on=E,F clean (no new warnings vs
   baseline); full pytest (-n 12 default) green as the per-commit final gate. Migrations
   (if any) round-trip upgrade->downgrade->upgrade cleanly; destructive changes get a Review
   docstring line and explicit developer approval before authoring.
6. Stay in scope. Out-of-scope issues, gold-plating opportunities, or refactors you noticed but did not
   perform MUST be flagged in the work summary with file:line and a one-sentence reason for not acting.
   Do not silently fold them in. Items that are not directly handled by a future commit need to be
   added to docs/audits/financial_calculations/remediation_follow_up.md. Any trivial items offer to
   address as a separate commit.
7. Do not push. After the work is green, present the work summary and ASK whether to commit
   and push to dev (this triggers CI; PR-to-main is required for promotion).

Work summary format (use these labels verbatim):
A. Verification: claims you re-verified; any drift found.
B. Files changed: list with one-line purpose each.
C. Tests added/modified: ID, name, hand-computed Decimal + arithmetic comment.
D. Re-pinned tests: finding ID + arithmetic per assertion, or "none".
E. Targeted-suite and full-suite final summary lines, verbatim from pytest.
F. Pylint final line, verbatim.
G. Migrations: upgrade->downgrade->upgrade result, or "n/a".
H. Invariants: which existing regression locks stayed green; which new locks added.
I. Discovered refinements folded in: each with rationale.
J. OUT OF SCOPE -- flagged, not fixed: file:line + reason per item + F-# entry in remediation_follow_up.md.
K. Open questions / assumptions made.
L. Proposed commit message (format: <type>(<scope>): <what> with the Co-Authored-By trailer
   per CLAUDE.md).
M. Ask: "Ready to commit and push to dev?"

Specific verification gates for this commit:
- After your changes: `grep -rn "calculate_balances" app/routes app/services | grep -v
  balance_resolver | grep -v loan_resolver | grep -v balance_calculator` returns empty
  (no external live callers of the seam-bearing engine; only the resolver and the loan-
  side wrapper call it internally).
- For the symptom tuple, net worth + /investment holdings + /retirement projection all
  show the same Decimal as the grid.

If anything is unclear, ASK. Do NOT route the loan-side reads through balance_resolver in
this commit -- that is Commit 15 with loan_resolver. If a route is ambiguous (checking
vs loan branch), read until you understand which branch you are touching.
```

---

### Commit 9 -- `fix(calendar): month-end balance via canonical balance-as-of-date (E-27)`

**Prereqs on dev:** 5. **Closes:** HIGH-02 / W-277 (entries-unaware month-end PLUS period-selection
off-by-up-to-13-days).

```text
You are executing Commit 9 of the Shekel financial-calculation audit remediation in a fresh
session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/audits/financial_calculations/remediation_plan.md (Sections 0-7; Section 9 "Commit
  9" A-H)
- @CLAUDE.md, @docs/coding-standards.md, @docs/testing-standards.md
- @docs/audits/financial_calculations/08_findings.md (HIGH-02)
- @docs/audits/financial_calculations/03_consistency.md (W-277 details)
- @app/services/calendar_service.py (the file being fixed; _compute_month_end_balance)
- @app/services/balance_resolver.py

Objective: extend balance_resolver with balance_as_of_date(account, scenario_id, as_of:
date) -> Decimal (E-27). It resolves the anchor, projects forward through periods, and
within the period containing as_of applies the entry-aware reduction only for entries
dated on/before as_of. Replace calendar_service._compute_month_end_balance with a call
to balance_as_of_date at the true calendar month-end date. Delete the stale period-
selection loop that picked "last period ending on or before month-end" (up to ~13 days
stale). Re-pin calendar month-end assertions.

Production files this commit touches:
- app/services/balance_resolver.py (add balance_as_of_date)
- app/services/calendar_service.py (replace _compute_month_end_balance body)
- tests/test_services/test_calendar_service.py
- tests/test_services/test_balance_resolver.py (new balance_as_of_date tests)

Apply these rules (the plan's Section 1 is the authoritative version):
1. The plan's specification for this commit (Section 9, subsections A through H) is the
   floor, not the ceiling. If verification surfaces extra in-scope refinements, fold them
   in and explain in the work summary.
2. Trust-but-verify: re-grep every cited symbol; read every file you will change in full;
   confirm the audit/plan claim still holds against current code before editing. If reality
   has drifted, stop and report in the work summary before continuing.
3. No shortcuts, no band-aid fixes. Fix root causes. Decimal money from strings, IDs and
   semantic booleans for business logic (never name strings), DRY/SOLID, fully normalized
   schema, pythonic type-hinted code, specific exceptions, substantive docstrings, no
   Unicode em/en dashes (use -- or -).
4. Never modify a test to make it pass except for the documented exception in plan Section 1
   rule 2: tests pinning a shipping wrong number this finding proves wrong. Every re-pinned
   assertion gets a comment naming the finding ID and the hand-computed arithmetic. List
   each in the work summary.
5. Targeted pytest during edits; pylint app/ --fail-on=E,F clean (no new warnings vs
   baseline); full pytest (-n 12 default) green as the per-commit final gate. Migrations
   (if any) round-trip upgrade->downgrade->upgrade cleanly; destructive changes get a Review
   docstring line and explicit developer approval before authoring.
6. Stay in scope. Out-of-scope issues, gold-plating opportunities, or refactors you noticed but did not
   perform MUST be flagged in the work summary with file:line and a one-sentence reason for not acting.
   Do not silently fold them in. Items that are not directly handled by a future commit need to be
   added to docs/audits/financial_calculations/remediation_follow_up.md. Any trivial items offer to
   address as a separate commit.
7. Do not push. After the work is green, present the work summary and ASK whether to commit
   and push to dev (this triggers CI; PR-to-main is required for promotion).

Work summary format (use these labels verbatim):
A. Verification: claims you re-verified; any drift found.
B. Files changed: list with one-line purpose each.
C. Tests added/modified: ID, name, hand-computed Decimal + arithmetic comment.
D. Re-pinned tests: finding ID + arithmetic per assertion, or "none".
E. Targeted-suite and full-suite final summary lines, verbatim from pytest.
F. Pylint final line, verbatim.
G. Migrations: upgrade->downgrade->upgrade result, or "n/a".
H. Invariants: which existing regression locks stayed green; which new locks added.
I. Discovered refinements folded in: each with rationale.
J. OUT OF SCOPE -- flagged, not fixed: file:line + reason per item + F-# entry in remediation_follow_up.md.
K. Open questions / assumptions made.
L. Proposed commit message (format: <type>(<scope>): <what> with the Co-Authored-By trailer
   per CLAUDE.md).
M. Ask: "Ready to commit and push to dev?"

Specific verification gates for this commit:
- A test where the calendar month-end falls MID-period asserts the calendar balance equals
  balance_as_of_date(month_end), not the last-period-end balance.
- A test where the month-end coincides with a period boundary asserts the calendar value
  equals balances_for at that period (cross-check).
- A test with an entry dated AFTER month-end asserts that entry is NOT yet reflected.

If anything is unclear, ASK.
```

---

### Commit 10 -- `fix(grid): period_subtotal through canonical producer (Q-10, E-25)`

**Prereqs on dev:** 5. **Closes:** F-002 Pair C / F-004 (UNKNOWN -> AGREE under Q-10's E-25
resolution).

```text
You are executing Commit 10 of the Shekel financial-calculation audit remediation in a
fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/audits/financial_calculations/remediation_plan.md (Sections 0-7; Section 9 "Commit
  10" A-H)
- @CLAUDE.md, @docs/coding-standards.md, @docs/testing-standards.md
- @docs/audits/financial_calculations/08_findings.md
- @docs/audits/financial_calculations/03_consistency.md (F-002 Pair C, F-004, Q-10)
- @app/routes/grid.py (inline period_subtotal loop, the same-page divergence vs the
  entry-aware balance row)
- @app/routes/obligations.py (also computes a period_subtotal-equivalent inline)
- @app/services/balance_resolver.py

Objective: add balance_resolver.period_subtotal(account, scenario_id, period) using the
same entry-aware reduction and the shared status predicate. Route grid.py's inline
subtotal loop and obligations.py's manual subtotal through it. Q-10 is resolved by E-25:
the subtotal is the entry-aware sum of balance-contributing items, so balance[p] -
balance[p-1] reconciles to subtotal[p].net by construction. Delete the inline loops.

Production files this commit touches:
- app/services/balance_resolver.py (period_subtotal)
- app/routes/grid.py (replace inline subtotal loop)
- app/routes/obligations.py (route through period_subtotal)
- tests/test_routes/test_grid.py (re-pin subtotal assertions; add the relationship test)

Apply these rules (the plan's Section 1 is the authoritative version):
1. The plan's specification for this commit (Section 9, subsections A through H) is the
   floor, not the ceiling. If verification surfaces extra in-scope refinements, fold them
   in and explain in the work summary.
2. Trust-but-verify: re-grep every cited symbol; read every file you will change in full;
   confirm the audit/plan claim still holds against current code before editing. If reality
   has drifted, stop and report in the work summary before continuing.
3. No shortcuts, no band-aid fixes. Fix root causes. Decimal money from strings, IDs and
   semantic booleans for business logic (never name strings), DRY/SOLID, fully normalized
   schema, pythonic type-hinted code, specific exceptions, substantive docstrings, no
   Unicode em/en dashes (use -- or -).
4. Never modify a test to make it pass except for the documented exception in plan Section 1
   rule 2: tests pinning a shipping wrong number this finding proves wrong. Every re-pinned
   assertion gets a comment naming the finding ID and the hand-computed arithmetic. List
   each in the work summary.
5. Targeted pytest during edits; pylint app/ --fail-on=E,F clean (no new warnings vs
   baseline); full pytest (-n 12 default) green as the per-commit final gate. Migrations
   (if any) round-trip upgrade->downgrade->upgrade cleanly; destructive changes get a Review
   docstring line and explicit developer approval before authoring.
6. Stay in scope. Out-of-scope issues, gold-plating opportunities, or refactors you noticed but did not
   perform MUST be flagged in the work summary with file:line and a one-sentence reason for not acting.
   Do not silently fold them in. Items that are not directly handled by a future commit need to be
   added to docs/audits/financial_calculations/remediation_follow_up.md. Any trivial items offer to
   address as a separate commit.
7. Do not push. After the work is green, present the work summary and ASK whether to commit
   and push to dev (this triggers CI; PR-to-main is required for promotion).

Re-pinned tests: any grid subtotal assertion that pinned the raw-effective-amount value
becomes the entry-aware value, with finding F-002 / F-004 and arithmetic in a comment.

Work summary format (use these labels verbatim):
A. Verification: claims you re-verified; any drift found.
B. Files changed: list with one-line purpose each.
C. Tests added/modified: ID, name, hand-computed Decimal + arithmetic comment.
D. Re-pinned tests: finding ID + arithmetic per assertion, or "none".
E. Targeted-suite and full-suite final summary lines, verbatim from pytest.
F. Pylint final line, verbatim.
G. Migrations: upgrade->downgrade->upgrade result, or "n/a".
H. Invariants: which existing regression locks stayed green; which new locks added.
I. Discovered refinements folded in: each with rationale.
J. OUT OF SCOPE -- flagged, not fixed: file:line + reason per item + F-# entry in remediation_follow_up.md.
K. Open questions / assumptions made.
L. Proposed commit message (format: <type>(<scope>): <what> with the Co-Authored-By trailer
   per CLAUDE.md).
M. Ask: "Ready to commit and push to dev?"

Specific verification gates for this commit:
- A new test asserts balance[p] - balance[p-1] == subtotal[p].net exactly on a grid
  fixture with a Projected envelope expense carrying cleared entries (this is the
  property the previous inline loop violated).
- `grep -n "sum.*effective_amount\|sum.*estimated_amount" app/routes/grid.py app/routes/
  obligations.py` shows no inline subtotal arithmetic remains.

If anything is unclear, ASK.
```

---

### Commit 11 -- `test(integration): cross-page balance-equality regression lock (HIGH-01)`

**Prereqs on dev:** 5, 6, 7, 8, 9, 10. **Closes:** HIGH-01 (the falsifying test the project never
had).

```text
You are executing Commit 11 of the Shekel financial-calculation audit remediation in a
fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/audits/financial_calculations/remediation_plan.md (Sections 0-7; Section 9 "Commit
  11" A-H; Section 10 verification walkthrough)
- @CLAUDE.md, @docs/coding-standards.md, @docs/testing-standards.md
- @docs/audits/financial_calculations/08_findings.md (HIGH-01)
- @docs/audits/financial_calculations/07_test_gaps.md (Part 7.B cross-page meta-gap)
- @tests/conftest.py (existing fixtures you will compose: seed_user, seed_periods_today,
  account/scenario/transaction helpers)
- @app/services/balance_resolver.py
- The route handlers for the six surfaces being asserted equal: grid, /savings,
  /accounts checking detail, dashboard, year-end net-worth per-account, calendar

Objective: create tests/test_integration/test_cross_page_balance_equality.py with the
PT-01 fixture (an account with the symptom tuple: anchor 614.29, Projected envelope
expense estimated 500.00 with three cleared debit entries summing 45.71, zero credits,
zero uncleared) plus a small parameter matrix (zero anchor, negative balance, credit-only
entries, uncleared-floor). For each parameter, render or call each of the six surfaces and
assert all return identical Decimal. Add a subtotal-reconciliation assertion on every
page. Add a "seam re-introduction" detection test: monkeypatch one consumer to bypass
balance_resolver; assert the invariant test FAILS (proving the lock bites).

Production files this commit touches:
- tests/test_integration/test_cross_page_balance_equality.py (new)
- tests/conftest.py (add seed_cross_page_account fixture; reuse existing fixtures, do not
  invent parallel ones)

This is a test-only commit. No production code change is expected. If a production code
change is required to make the invariant pass, STOP and report -- that means one of
Commits 5-10 left a residual divergence that must be fixed first, not papered over here.

Apply these rules (the plan's Section 1 is the authoritative version):
1. The plan's specification for this commit (Section 9, subsections A through H) is the
   floor, not the ceiling. If verification surfaces extra in-scope refinements, fold them
   in and explain in the work summary.
2. Trust-but-verify: re-grep every cited symbol; read every file you will change in full;
   confirm the audit/plan claim still holds against current code before editing. If reality
   has drifted, stop and report in the work summary before continuing.
3. No shortcuts, no band-aid fixes. Fix root causes. Decimal money from strings, IDs and
   semantic booleans for business logic (never name strings), DRY/SOLID, fully normalized
   schema, pythonic type-hinted code, specific exceptions, substantive docstrings, no
   Unicode em/en dashes (use -- or -).
4. Never modify a test to make it pass except for the documented exception in plan Section 1
   rule 2: tests pinning a shipping wrong number this finding proves wrong. Every re-pinned
   assertion gets a comment naming the finding ID and the hand-computed arithmetic. List
   each in the work summary.
5. Targeted pytest during edits; pylint app/ --fail-on=E,F clean (no new warnings vs
   baseline); full pytest (-n 12 default) green as the per-commit final gate. Migrations
   (if any) round-trip upgrade->downgrade->upgrade cleanly; destructive changes get a Review
   docstring line and explicit developer approval before authoring.
6. Stay in scope. Out-of-scope issues, gold-plating opportunities, or refactors you noticed but did not
   perform MUST be flagged in the work summary with file:line and a one-sentence reason for not acting.
   Do not silently fold them in. Items that are not directly handled by a future commit need to be
   added to docs/audits/financial_calculations/remediation_follow_up.md. Any trivial items offer to
   address as a separate commit.
7. Do not push. After the work is green, present the work summary and ASK whether to commit
   and push to dev (this triggers CI; PR-to-main is required for promotion).

Work summary format (use these labels verbatim):
A. Verification: claims you re-verified; any drift found.
B. Files changed: list with one-line purpose each.
C. Tests added/modified: ID, name, hand-computed Decimal + arithmetic comment.
D. Re-pinned tests: finding ID + arithmetic per assertion, or "none".
E. Targeted-suite and full-suite final summary lines, verbatim from pytest.
F. Pylint final line, verbatim.
G. Migrations: upgrade->downgrade->upgrade result, or "n/a".
H. Invariants: which existing regression locks stayed green; which new locks added.
I. Discovered refinements folded in: each with rationale.
J. OUT OF SCOPE -- flagged, not fixed: file:line + reason per item + F-# entry in remediation_follow_up.md.
K. Open questions / assumptions made.
L. Proposed commit message (format: <type>(<scope>): <what> with the Co-Authored-By trailer
   per CLAUDE.md).
M. Ask: "Ready to commit and push to dev?"

Specific verification gates for this commit:
- All parameter rows green.
- The seam-injection negative-case test FAILS when the bypass is in place and PASSES when
  removed (proves the lock is real, not a coincidence).

If anything is unclear, ASK.
```

## Group C -- Loan source of truth (CRIT-02 family + HIGH-08)

### Commit 12 -- `feat(loan): append-only loan_anchor_events table + backfill (E-18)`

**Prereqs on dev:** 1 (round_money used during backfill verification); independent of the balance
group. **Closes:** CRIT-02 / F-014 (E-18 infrastructure for the loan resolver in Commit 13).

```text
You are executing Commit 12 of the Shekel financial-calculation audit remediation in a
fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/audits/financial_calculations/remediation_plan.md (Sections 0-7; Section 9 "Commit
  12" A-H; Section 2 design decision D-A for the table rationale)
- @CLAUDE.md (especially Transfer Invariants and the audit-trigger section)
- @docs/coding-standards.md (Migrations section, Audit Triggers section -- new audited
  table requires AUDITED_TABLES + rebuild)
- @docs/testing-standards.md (Building the test template)
- @docs/audits/financial_calculations/08_findings.md (CRIT-02)
- @app/models/loan_params.py (LoanParams; immutable origination data lives here)
- @app/models/account.py (AccountAnchorHistory -- the existing checking-side analog; mirror
  its append-only + unique-index pattern)
- @app/services/loan_payment_service.py (get_payment_history; the confirmed-payment stream
  the resolver in Commit 13 will replay forward from anchor)
- @app/services/amortization_engine.py (generate_schedule already accepts anchor_balance/
  anchor_date; do not modify it in this commit)
- @app/enums.py, @app/ref_seeds.py, @app/audit_infrastructure.py (the AUDITED_TABLES list)

Objective: introduce budget.loan_anchor_events as an append-only table that anchors a loan
to a dated balance assertion. Columns: id, account_id (FK CASCADE, NOT NULL), anchor_date
(Date, NOT NULL), anchor_balance (Numeric(12,2), NOT NULL, CHECK >= 0), source_id (FK to a
new ref enum LoanAnchorSource with values origination, user_trueup -- IDs, never names in
code), created_at (CreatedAtMixin). Unique functional index mirroring AccountAnchorHistory
(account_id, anchor_date, anchor_balance, ((created_at AT TIME ZONE 'UTC')::date)) to
prevent same-day duplicates. The table is structurally append-only: the model exposes no
update or delete API, and only inserts happen in code (forensic immutability matches the
project's audit philosophy). Migration backfills every existing loan account with an
origination event from immutable LoanParams (origination_date, original_principal) and,
when stored current_principal differs from the confirmed-payment replay from origination,
a user_trueup event (today, current_principal) so display continuity holds when the
resolver lands in Commit 13. Register the table in AUDITED_TABLES; the entrypoint trigger-
count health check expects the new trigger after rebuild.

Production files this commit touches (re-confirm by grep before editing):
- app/models/loan_anchor_event.py (new)
- app/enums.py (LoanAnchorSourceEnum)
- app/ref_seeds.py (seed LoanAnchorSource ref values)
- app/audit_infrastructure.py (AUDITED_TABLES += 'loan_anchor_events')
- migrations/versions/<auto>_create_loan_anchor_events.py (new; create table + ref seed +
  backfill)
- tests/test_models/test_loan_anchor_event.py (new)
- tests/test_models/test_loan_anchor_backfill.py (new)

Apply these rules (the plan's Section 1 is the authoritative version):
1. The plan's specification for this commit (Section 9, subsections A through H) is the
   floor, not the ceiling. If verification surfaces extra in-scope refinements, fold them
   in and explain in the work summary.
2. Trust-but-verify: re-grep every cited symbol; read every file you will change in full;
   confirm the audit/plan claim still holds against current code before editing. If reality
   has drifted, stop and report in the work summary before continuing.
3. No shortcuts, no band-aid fixes. Fix root causes. Decimal money from strings, IDs and
   semantic booleans for business logic (never name strings), DRY/SOLID, fully normalized
   schema, pythonic type-hinted code, specific exceptions, substantive docstrings, no
   Unicode em/en dashes (use -- or -).
4. Never modify a test to make it pass except for the documented exception in plan Section 1
   rule 2: tests pinning a shipping wrong number this finding proves wrong. Every re-pinned
   assertion gets a comment naming the finding ID and the hand-computed arithmetic. List
   each in the work summary.
5. Targeted pytest during edits; pylint app/ --fail-on=E,F clean (no new warnings vs
   baseline); full pytest (-n 12 default) green as the per-commit final gate. Migrations
   (if any) round-trip upgrade->downgrade->upgrade cleanly; destructive changes get a Review
   docstring line and explicit developer approval before authoring.
6. Stay in scope. Out-of-scope issues, gold-plating opportunities, or refactors you noticed but did not
   perform MUST be flagged in the work summary with file:line and a one-sentence reason for not acting.
   Do not silently fold them in. Items that are not directly handled by a future commit need to be
   added to docs/audits/financial_calculations/remediation_follow_up.md. Any trivial items offer to
   address as a separate commit.
7. Do not push. After the work is green, present the work summary and ASK whether to commit
   and push to dev (this triggers CI; PR-to-main is required for promotion).

Migration constraints (non-negotiable):
- The new table is not a populated-table NOT-NULL alteration, but the backfill IS a
  populated-data operation; document the derivation in the migration docstring.
- Carry a Review: docstring line ("Review: solo developer, <date> (audit financial_
  calculations CRIT-02/E-18, new audited table)") per the coding standard.
- Downgrade drops the table and the ref values; backfill is reproducible from LoanParams
  so this is reversible. Test upgrade -> downgrade -> upgrade cleanly.
- After the migration, `python scripts/build_test_template.py` (the trigger-count expected
  by the entrypoint must be the new count; if the entrypoint pin is configured anywhere,
  update it as part of this commit).

Work summary format (use these labels verbatim):
A. Verification: claims you re-verified; any drift found.
B. Files changed: list with one-line purpose each.
C. Tests added/modified: ID, name, hand-computed Decimal + arithmetic comment.
D. Re-pinned tests: finding ID + arithmetic per assertion, or "none".
E. Targeted-suite and full-suite final summary lines, verbatim from pytest.
F. Pylint final line, verbatim.
G. Migrations: upgrade->downgrade->upgrade result, or "n/a".
H. Invariants: which existing regression locks stayed green; which new locks added.
I. Discovered refinements folded in: each with rationale.
J. OUT OF SCOPE -- flagged, not fixed: file:line + reason per item + F-# entry in remediation_follow_up.md.
K. Open questions / assumptions made.
L. Proposed commit message (format: <type>(<scope>): <what> with the Co-Authored-By trailer
   per CLAUDE.md).
M. Ask: "Ready to commit and push to dev?"

Specific verification gates for this commit:
- A test asserts the model exposes no update/delete API (programmatic enforcement, not
  just convention).
- A test asserts every existing loan account has at least one origination row after
  upgrade, with anchor_date == origination_date and anchor_balance == original_principal.
- A test asserts a trueup row was inserted ONLY for loans whose stored current_principal
  diverged from a from-origination confirmed-payment replay (you may use the existing
  debt_strategy._compute_real_principal for the fixed-rate comparison since the loan
  resolver does not exist yet; explain this dependency in the work summary).
- AUDITED_TABLES contains the new table; rebuild test template; suite green.

If anything is unclear, ASK. Do not generalize AccountAnchorHistory in this commit (D-A
chose a parallel table; that is a deliberate scope decision, not a TODO).
```

---

### Commit 13 -- `feat(loan): pure event-derived loan resolver (E-18)`

**Prereqs on dev:** 1, 12. **Closes:** CRIT-02 / F-013, F-015, F-026 (symptoms #2 and #4).

```text
You are executing Commit 13 of the Shekel financial-calculation audit remediation in a
fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/audits/financial_calculations/remediation_plan.md (Sections 0-7; Section 3 R-2 for
  the existing event-replay infrastructure this commit consolidates; Section 9 "Commit 13"
  A-H; Section 11 hand-computed reconciliation appendix for the ARM worked examples)
- @CLAUDE.md
- @docs/coding-standards.md, @docs/testing-standards.md
- @docs/audits/financial_calculations/08_findings.md (CRIT-02)
- @docs/audits/financial_calculations/05_symptoms.md (symptoms #2 and #4 hypothesis trees
  with the $2,460.45 -> $2,463.28 worked example for the fixed-window creep)
- @app/services/amortization_engine.py (generate_schedule, calculate_monthly_payment,
  calculate_remaining_months, the PaymentRecord and RateChangeRecord dataclasses, the ARM
  branch that reads stored current_principal verbatim; do not rewrite this -- the resolver
  feeds it the anchor-derived inputs)
- @app/services/loan_payment_service.py (get_payment_history, load_loan_context)
- @app/models/loan_anchor_event.py (Commit 12)
- @app/models/loan_params.py, @app/models/rate_history.py (or wherever RateHistory lives)
- @app/utils/money.py (Commit 1; the only rounding boundary)
- @app/services/debt_strategy.py (_compute_real_principal -- this commit will subsume it
  in Commit 15; read it now to understand the fixed-rate replay that already works)

Objective: create app/services/loan_resolver.py with resolve_loan(loan_params,
anchor_events, payments, rate_changes, as_of) -> LoanState, a frozen dataclass
(current_balance, monthly_payment, schedule, payoff_date, total_interest). Pure function:
takes plain data, returns plain data, no DB/Flask. Algorithm: (1) pick the latest
LoanAnchorEvent as (anchor_balance, anchor_date); (2) replay only is_confirmed payments
whose date > anchor_date, principal-only reduction (interest/escrow do not reduce
principal -- E-01); (3) for an ARM whose anchor_date is inside [origination, origination
+ arm_first_adjustment_months), compute the monthly payment ONCE from anchor_balance over
remaining contractual term as of anchor_date and hold it constant for every as_of inside
the window (the E-02 fixed-window invariant -- this is the symptom #4 fix); (4) outside
the window, amortize the current balance at the rate in effect for as_of over the
remaining months; (5) round_money is the only rounding boundary. Reuse generate_schedule
for the full schedule (it already accepts anchor_balance/anchor_date/payments/rate_changes
-- do not rewrite, per CLAUDE.md rule 10).

Production files this commit touches:
- app/services/loan_resolver.py (new)
- tests/test_services/test_loan_resolver.py (new, hand-computed expectations)

Apply these rules (the plan's Section 1 is the authoritative version):
1. The plan's specification for this commit (Section 9, subsections A through H) is the
   floor, not the ceiling. If verification surfaces extra in-scope refinements, fold them
   in and explain in the work summary.
2. Trust-but-verify: re-grep every cited symbol; read every file you will change in full;
   confirm the audit/plan claim still holds against current code before editing. If reality
   has drifted, stop and report in the work summary before continuing.
3. No shortcuts, no band-aid fixes. Fix root causes. Decimal money from strings, IDs and
   semantic booleans for business logic (never name strings), DRY/SOLID, fully normalized
   schema, pythonic type-hinted code, specific exceptions, substantive docstrings, no
   Unicode em/en dashes (use -- or -).
4. Never modify a test to make it pass except for the documented exception in plan Section 1
   rule 2: tests pinning a shipping wrong number this finding proves wrong. Every re-pinned
   assertion gets a comment naming the finding ID and the hand-computed arithmetic. List
   each in the work summary.
5. Targeted pytest during edits; pylint app/ --fail-on=E,F clean (no new warnings vs
   baseline); full pytest (-n 12 default) green as the per-commit final gate. Migrations
   (if any) round-trip upgrade->downgrade->upgrade cleanly; destructive changes get a Review
   docstring line and explicit developer approval before authoring.
6. Stay in scope. Out-of-scope issues, gold-plating opportunities, or refactors you noticed but did not
   perform MUST be flagged in the work summary with file:line and a one-sentence reason for not acting.
   Do not silently fold them in. Items that are not directly handled by a future commit need to be
   added to docs/audits/financial_calculations/remediation_follow_up.md. Any trivial items offer to
   address as a separate commit.
7. Do not push. After the work is green, present the work summary and ASK whether to commit
   and push to dev (this triggers CI; PR-to-main is required for promotion).

Services-boundary gate: loan_resolver imports nothing from Flask; no db.session writes.

Work summary format (use these labels verbatim):
A. Verification: claims you re-verified; any drift found.
B. Files changed: list with one-line purpose each.
C. Tests added/modified: ID, name, hand-computed Decimal + arithmetic comment.
D. Re-pinned tests: finding ID + arithmetic per assertion, or "none".
E. Targeted-suite and full-suite final summary lines, verbatim from pytest.
F. Pylint final line, verbatim.
G. Migrations: upgrade->downgrade->upgrade result, or "n/a".
H. Invariants: which existing regression locks stayed green; which new locks added.
I. Discovered refinements folded in: each with rationale.
J. OUT OF SCOPE -- flagged, not fixed: file:line + reason per item + F-# entry in remediation_follow_up.md.
K. Open questions / assumptions made.
L. Proposed commit message (format: <type>(<scope>): <what> with the Co-Authored-By trailer
   per CLAUDE.md).
M. Ask: "Ready to commit and push to dev?"

Specific verification gates for this commit (NON-NEGOTIABLE; symptoms #2/#4 hinge here):
- Stability lock: a test computes resolve_loan().monthly_payment for every month in
  [origination, origination + arm_first_adjustment_months) on a 5/5 ARM ($400k, 6%, 360mo)
  and asserts byte-identical Decimal across all 60 months. Pre-fix this varied; post-fix
  it must NOT.
- Hand-computed Decimal: at least one ARM test pins monthly_payment to the value computed
  by hand from the amortization formula, with the arithmetic in a comment.
- Confirmed-payment reduction: a test with one $1888.36 P&I payment confirmed after the
  anchor asserts balance == anchor - principal_portion (hand-computed: 300000 - (1888.36 -
  300000*0.005) = 299611.64).
- Projected (unconfirmed) payments do NOT reduce balance.
- Anchor trueup resets the replay: a user_trueup event after N confirmed payments makes
  resolve_loan ignore the pre-trueup payments.
- Zero-rate loan: payment = principal / n; no div-by-zero.
- Re-pin the audit's symptom hand-computations in the work summary's section H (invariants)
  to show the numbers reconcile.

If anything is unclear, ASK. Do NOT rewrite generate_schedule; if the existing engine has
a behavior the resolver needs and lacks, document it as an out-of-scope finding in the
work summary -- you may extend the engine ONLY for additive, non-behavior-changing inputs
the resolver requires (e.g. honoring arm_first_adjustment_months for the window guard if
that is genuinely missing). Any such extension is itself in scope if it is the root cause.
```

---

### Commit 14 -- `test(loan): settled transfer reduces resolved principal (symptom #3)`

**Prereqs on dev:** 13. **Closes:** CRIT-02 symptom #3 (the most important behavioral lock; not
assumed -- proven end-to-end).

```text
You are executing Commit 14 of the Shekel financial-calculation audit remediation in a
fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/audits/financial_calculations/remediation_plan.md (Sections 0-7; Section 9 "Commit
  14" A-H; Section 10 walkthrough item for symptom #3)
- @CLAUDE.md (Transfer Invariants are load-bearing here)
- @docs/coding-standards.md, @docs/testing-standards.md
- @docs/audits/financial_calculations/05_symptoms.md (symptom #3 hypothesis tree;
  E-01 transfer split invariant)
- @app/services/transfer_service.py (the real settle path; you drive it from the test,
  not a manual status set)
- @app/services/loan_resolver.py (Commit 13)
- @app/routes/transactions.py (mark-done flow for income shadow -> RECEIVED)
- @tests/conftest.py (fixtures: seed_user, account/loan creation helpers)

Objective: create tests/test_integration/test_loan_principal_settles.py that drives the
real user workflow (create loan account, create a PITI transfer, settle it via the real
mark-done path) and asserts (a) the loan resolver's current_balance dropped by exactly
the principal portion of the payment, (b) escrow and interest portions did NOT reduce
principal (E-01 split), (c) projected (unconfirmed) transfers do not reduce principal,
(d) cumulative settlements compose correctly. This is a test-only commit unless the
test reveals a real defect in the confirmed-payment feed -- if so, STOP and report; do
not paper over with a band-aid.

Production files this commit touches:
- tests/test_integration/test_loan_principal_settles.py (new)
- (potentially) targeted root-cause fix if the feed misses settled shadows -- report
  before changing production code

Apply these rules (the plan's Section 1 is the authoritative version):
1. The plan's specification for this commit (Section 9, subsections A through H) is the
   floor, not the ceiling. If verification surfaces extra in-scope refinements, fold them
   in and explain in the work summary.
2. Trust-but-verify: re-grep every cited symbol; read every file you will change in full;
   confirm the audit/plan claim still holds against current code before editing. If reality
   has drifted, stop and report in the work summary before continuing.
3. No shortcuts, no band-aid fixes. Fix root causes. Decimal money from strings, IDs and
   semantic booleans for business logic (never name strings), DRY/SOLID, fully normalized
   schema, pythonic type-hinted code, specific exceptions, substantive docstrings, no
   Unicode em/en dashes (use -- or -).
4. Never modify a test to make it pass except for the documented exception in plan Section 1
   rule 2: tests pinning a shipping wrong number this finding proves wrong. Every re-pinned
   assertion gets a comment naming the finding ID and the hand-computed arithmetic. List
   each in the work summary.
5. Targeted pytest during edits; pylint app/ --fail-on=E,F clean (no new warnings vs
   baseline); full pytest (-n 12 default) green as the per-commit final gate. Migrations
   (if any) round-trip upgrade->downgrade->upgrade cleanly; destructive changes get a Review
   docstring line and explicit developer approval before authoring.
6. Stay in scope. Out-of-scope issues, gold-plating opportunities, or refactors you noticed but did not
   perform MUST be flagged in the work summary with file:line and a one-sentence reason for not acting.
   Do not silently fold them in. Items that are not directly handled by a future commit need to be
   added to docs/audits/financial_calculations/remediation_follow_up.md. Any trivial items offer to
   address as a separate commit.
7. Do not push. After the work is green, present the work summary and ASK whether to commit
   and push to dev (this triggers CI; PR-to-main is required for promotion).

Note for this commit (CLAUDE.md rule 4 "never ignore a problem" emphasis): if you discover
a real bug while writing the integration test, fix it at the root cause inside this commit
and document the root-cause fix prominently in the work summary. Do not paper over with a
band-aid; do not push the fix to a later commit.

Work summary format (use these labels verbatim):
A. Verification: claims you re-verified; any drift found.
B. Files changed: list with one-line purpose each.
C. Tests added/modified: ID, name, hand-computed Decimal + arithmetic comment.
D. Re-pinned tests: finding ID + arithmetic per assertion, or "none".
E. Targeted-suite and full-suite final summary lines, verbatim from pytest.
F. Pylint final line, verbatim.
G. Migrations: upgrade->downgrade->upgrade result, or "n/a".
H. Invariants: which existing regression locks stayed green; which new locks added.
I. Discovered refinements folded in: each with rationale.
J. OUT OF SCOPE -- flagged, not fixed: file:line + reason per item + F-# entry in remediation_follow_up.md.
K. Open questions / assumptions made.
L. Proposed commit message (format: <type>(<scope>): <what> with the Co-Authored-By trailer
   per CLAUDE.md).
M. Ask: "Ready to commit and push to dev?"

Specific verification gates for this commit:
- E-01 split assertion: checking balance dropped by FULL P+I+escrow (handled by Commits
  5-10 producer, asserted here as a cross-cut); loan resolver balance dropped by P only.
- Settling N transfers reduces the resolved principal cumulatively by sum of principal
  portions; hand-compute and pin.
- The card-display assertion (C14-5 / C15-6 in the plan) belongs in Commit 15 once
  consumers are routed -- do NOT add it here. Add only the resolver-level assertions.

If anything is unclear, ASK. Drive the test through the REAL transfer settle path
(transfer_service / mark-done route); a hand-set status is not a faithful reproduction.
```

---

### Commit 15 -- `refactor(loan): demote current_principal/interest_rate; route all consumers (E-18)`

**Prereqs on dev:** 13, 14. **Closes:** CRIT-02 / F-008, F-015, F-016 / symptom #5 loan facet;
HIGH-08 partial.

```text
You are executing Commit 15 of the Shekel financial-calculation audit remediation in a
fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/audits/financial_calculations/remediation_plan.md (Sections 0-7; Section 2 D-A for
  the additive-demotion decision; Section 9 "Commit 15" A-H)
- @CLAUDE.md
- @docs/coding-standards.md (Migrations -- alter to nullable IS additive, but it changes a
  long-standing NOT NULL contract; carry Review: docstring line, test downgrade)
- @docs/testing-standards.md
- @docs/audits/financial_calculations/08_findings.md (CRIT-02, F-008, F-015, F-016)
- @app/models/loan_params.py (the two columns being demoted; understand what currently
  reads them)
- @app/routes/loan.py (loan dashboard card and payoff/refinance/create_payment_transfer)
- @app/routes/debt_strategy.py (ARM _compute_real_principal returns stored verbatim)
- @app/services/savings_dashboard_service.py (debt card)
- @app/services/year_end_summary_service.py (net-worth schedule path)
- @app/services/loan_resolver.py (Commit 13)
- relevant test files

Objective: (1) Alter LoanParams.current_principal and LoanParams.interest_rate to
nullable=True with comments "non-authoritative seed; resolver is source of truth (E-18)"
-- additive migration, reversible. (2) Replace every DISPLAY READ of these columns with
loan_resolver.resolve_loan(...). After this commit, `grep -rn "params\.current_principal\|
\.current_principal" app/` shows reads only in the resolver/backfill (and the now-write-
only seed path in Commit 16) -- no display path reads the columns. (3) Re-pin loan
principal/payment assertions across loan, debt_strategy, savings_dashboard,
year_end_summary tests with findings F-008/F-015/F-016/symptom #5 and the arithmetic.

Production files this commit touches:
- app/models/loan_params.py (nullability + comments)
- app/routes/loan.py, app/routes/debt_strategy.py
- app/services/savings_dashboard_service.py, app/services/year_end_summary_service.py
  (loan/net-worth branch only -- the checking-balance branch was routed in Commit 8)
- migrations/versions/<auto>_demote_loan_columns.py (new; nullable=True alter; Review: line)
- tests/test_routes/test_loan.py, tests/test_routes/test_debt_strategy.py,
  tests/test_services/test_savings_dashboard_service.py,
  tests/test_services/test_year_end_summary_service.py

Apply these rules (the plan's Section 1 is the authoritative version):
1. The plan's specification for this commit (Section 9, subsections A through H) is the
   floor, not the ceiling. If verification surfaces extra in-scope refinements, fold them
   in and explain in the work summary.
2. Trust-but-verify: re-grep every cited symbol; read every file you will change in full;
   confirm the audit/plan claim still holds against current code before editing. If reality
   has drifted, stop and report in the work summary before continuing.
3. No shortcuts, no band-aid fixes. Fix root causes. Decimal money from strings, IDs and
   semantic booleans for business logic (never name strings), DRY/SOLID, fully normalized
   schema, pythonic type-hinted code, specific exceptions, substantive docstrings, no
   Unicode em/en dashes (use -- or -).
4. Never modify a test to make it pass except for the documented exception in plan Section 1
   rule 2: tests pinning a shipping wrong number this finding proves wrong. Every re-pinned
   assertion gets a comment naming the finding ID and the hand-computed arithmetic. List
   each in the work summary.
5. Targeted pytest during edits; pylint app/ --fail-on=E,F clean (no new warnings vs
   baseline); full pytest (-n 12 default) green as the per-commit final gate. Migrations
   (if any) round-trip upgrade->downgrade->upgrade cleanly; destructive changes get a Review
   docstring line and explicit developer approval before authoring.
6. Stay in scope. Out-of-scope issues, gold-plating opportunities, or refactors you noticed but did not
   perform MUST be flagged in the work summary with file:line and a one-sentence reason for not acting.
   Do not silently fold them in. Items that are not directly handled by a future commit need to be
   added to docs/audits/financial_calculations/remediation_follow_up.md. Any trivial items offer to
   address as a separate commit.
7. Do not push. After the work is green, present the work summary and ASK whether to commit
   and push to dev (this triggers CI; PR-to-main is required for promotion).

Re-pinned tests: list every assertion, with the finding ID and the arithmetic.

Work summary format (use these labels verbatim):
A. Verification: claims you re-verified; any drift found.
B. Files changed: list with one-line purpose each.
C. Tests added/modified: ID, name, hand-computed Decimal + arithmetic comment.
D. Re-pinned tests: finding ID + arithmetic per assertion, or "none".
E. Targeted-suite and full-suite final summary lines, verbatim from pytest.
F. Pylint final line, verbatim.
G. Migrations: upgrade->downgrade->upgrade result, or "n/a".
H. Invariants: which existing regression locks stayed green; which new locks added.
I. Discovered refinements folded in: each with rationale.
J. OUT OF SCOPE -- flagged, not fixed: file:line + reason per item + F-# entry in remediation_follow_up.md.
K. Open questions / assumptions made.
L. Proposed commit message (format: <type>(<scope>): <what> with the Co-Authored-By trailer
   per CLAUDE.md).
M. Ask: "Ready to commit and push to dev?"

Specific verification gates for this commit:
- For a fixed-rate loan with confirmed payments: /accounts loan card balance == /savings
  account card balance == /savings debt card balance == net-worth liability balance == the
  resolver's current_balance (one Decimal).
- For an ARM in the fixed window: the displayed monthly_payment is identical to the
  hand-computed constant from Commit 13's stability lock (a stronger assertion than just
  "consistent across surfaces").
- C15-6 (the deferred card-display assertion from Commit 14): settling a transfer reduces
  the displayed Current Principal card.
- `grep -rn "\.current_principal" app/ | grep -v migrations | grep -v loan_resolver |
  grep -v loan_anchor_event` shows ONLY write/seed paths (Commit 16 will add the trueup
  event writer).

If anything is unclear, ASK.
```

---

### Commit 16 -- `feat(loan): principal edit becomes dated balance true-up event (E-18 UX)`

**Prereqs on dev:** 12, 15. **Closes:** CRIT-02 (E-18 UX; decision D-C: mirror checking
AccountAnchorHistory UX).

```text
You are executing Commit 16 of the Shekel financial-calculation audit remediation in a
fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/audits/financial_calculations/remediation_plan.md (Sections 0-7; Section 2 D-C for
  the UX rationale; Section 3 R-7 for the pre-extracted anchor_service helper this commit
  EXTENDS rather than copies; Section 9 "Commit 16" A-H)
- @CLAUDE.md, @docs/coding-standards.md, @docs/testing-standards.md
- @app/services/anchor_service.py (the canonical helper; you extend it for loan anchors --
  do NOT paste a third inline try/except block; the checking path's
  AnchorTrueUpOutcome / COMMITTED / STALE_CONFLICT / DUPLICATE_SAME_DAY contract is the
  shape your loan path must match)
- @tests/test_services/test_anchor_service.py (the eight checking-anchor pinned tests are
  the template you extend with loan-anchor cases)
- @app/routes/loan.py (current _PARAM_FIELDS / setattr current_principal flow being
  replaced)
- @app/routes/accounts.py (the inline_anchor_update / true_up routes are the call-site
  reference -- they wrap anchor_service.apply_anchor_true_up + outcome-switch; you mirror
  that composition, not the pre-extraction inline try/except shape)
- @app/templates/loan/dashboard.html (current scalar input)
- @app/templates/accounts/_anchor_cell.html and grid/_anchor_edit.html (the checking
  true-up partials; the form markup pattern to mirror)
- @app/schemas/validation.py (existing validation schemas to mirror; add the LoanAnchor
  schema)
- @app/models/loan_anchor_event.py (Commit 12)

Objective: replace the scalar "Current Principal" input on /accounts/<id>/loan with a
dated balance true-up form -- "Record loan balance as of date D, balance X" -- that on
submit appends a user_trueup LoanAnchorEvent (never UPDATE/DELETE; append-only). EXTEND
app/services/anchor_service.py to handle loan anchors (R-7: the checking path was
consolidated pre-emptively in commit d551f9c so this commit lands the third caller
without a third inline copy of the C-17 + F-103/C-22 try/except). Two viable shapes for
the extension: (a) parameterise apply_anchor_true_up with an account_kind discriminant +
the appropriate unique-index name, or (b) add a sibling apply_loan_anchor_true_up that
delegates to a private _apply_true_up_core helper shared with the checking path. Pick the
one that leaves the smaller call-site surface and matches the existing
AnchorTrueUpOutcome contract exactly. Mirror the checking AccountAnchorHistory true-up's
UI markup and the schema/validation idioms (DRY: do not invent a new pattern when one
exists). Marshmallow schema rejects date_in_future, date < origination_date, and balance
< 0. CSRF + POST required. interest_rate edits continue to flow through the existing
RateHistory path -- this commit does not touch that.

Production files this commit touches:
- app/services/anchor_service.py (EXTEND for loan anchors; preserve the checking path's
  byte-identical behavior)
- app/routes/loan.py
- app/schemas/validation.py (new LoanAnchorTrueupSchema)
- app/templates/loan/dashboard.html (replace the scalar input with the dated true-up form
  partial)
- tests/test_services/test_anchor_service.py (extend with loan-anchor cases mirroring the
  eight existing checking-anchor cases)
- tests/test_routes/test_loan.py (UX-level: response shape, template rendering, HX headers)

Apply these rules (the plan's Section 1 is the authoritative version):
1. The plan's specification for this commit (Section 9, subsections A through H) is the
   floor, not the ceiling. If verification surfaces extra in-scope refinements, fold them
   in and explain in the work summary.
2. Trust-but-verify: re-grep every cited symbol; read every file you will change in full;
   confirm the audit/plan claim still holds against current code before editing. If reality
   has drifted, stop and report in the work summary before continuing.
3. No shortcuts, no band-aid fixes. Fix root causes. Decimal money from strings, IDs and
   semantic booleans for business logic (never name strings), DRY/SOLID, fully normalized
   schema, pythonic type-hinted code, specific exceptions, substantive docstrings, no
   Unicode em/en dashes (use -- or -).
4. Never modify a test to make it pass except for the documented exception in plan Section 1
   rule 2: tests pinning a shipping wrong number this finding proves wrong. Every re-pinned
   assertion gets a comment naming the finding ID and the hand-computed arithmetic. List
   each in the work summary.
5. Targeted pytest during edits; pylint app/ --fail-on=E,F clean (no new warnings vs
   baseline); full pytest (-n 12 default) green as the per-commit final gate. Migrations
   (if any) round-trip upgrade->downgrade->upgrade cleanly; destructive changes get a Review
   docstring line and explicit developer approval before authoring.
6. Stay in scope. Out-of-scope issues, gold-plating opportunities, or refactors you noticed but did not
   perform MUST be flagged in the work summary with file:line and a one-sentence reason for not acting.
   Do not silently fold them in. Items that are not directly handled by a future commit need to be
   added to docs/audits/financial_calculations/remediation_follow_up.md. Any trivial items offer to
   address as a separate commit.
7. Do not push. After the work is green, present the work summary and ASK whether to commit
   and push to dev (this triggers CI; PR-to-main is required for promotion).

Work summary format (use these labels verbatim):
A. Verification: claims you re-verified; any drift found.
B. Files changed: list with one-line purpose each.
C. Tests added/modified: ID, name, hand-computed Decimal + arithmetic comment.
D. Re-pinned tests: finding ID + arithmetic per assertion, or "none".
E. Targeted-suite and full-suite final summary lines, verbatim from pytest.
F. Pylint final line, verbatim.
G. Migrations: upgrade->downgrade->upgrade result, or "n/a".
H. Invariants: which existing regression locks stayed green; which new locks added.
I. Discovered refinements folded in: each with rationale.
J. OUT OF SCOPE -- flagged, not fixed: file:line + reason per item + F-# entry in remediation_follow_up.md.
K. Open questions / assumptions made.
L. Proposed commit message (format: <type>(<scope>): <what> with the Co-Authored-By trailer
   per CLAUDE.md).
M. Ask: "Ready to commit and push to dev?"

Specific verification gates for this commit:
- A POST true-up creates a new LoanAnchorEvent; no prior row is mutated (assert append-
  only by post-condition).
- A subsequent render of /accounts loan card, /savings, net-worth, and the resolver all
  reflect the new anchor.
- 422 on future date, pre-origination date, negative balance.
- CSRF token enforcement.
- Dark mode + mobile breakpoint render the new form correctly (manual verification step).
- Reuse the checking partial if possible; if you create a new partial, justify the
  non-reuse in the work summary (DRY check).

If anything is unclear, ASK.
```

---

### Commit 17 -- `fix(loan): unify per-period/interest/payoff figures via resolver+round_money (HIGH-08)`

**Prereqs on dev:** 1, 13. (15 strongly recommended for clean baseline.) **Closes:** HIGH-08 /
F-017..F-023 (per-period principal/interest, total_interest, interest_saved, months_saved,
payoff_date).

```text
You are executing Commit 17 of the Shekel financial-calculation audit remediation in a
fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/audits/financial_calculations/remediation_plan.md (Sections 0-7; Section 9 "Commit
  17" A-H)
- @CLAUDE.md, @docs/coding-standards.md, @docs/testing-standards.md
- @docs/audits/financial_calculations/08_findings.md (HIGH-08)
- @docs/audits/financial_calculations/03_consistency.md (F-017..F-023 details with the
  banker's-vs-half-up rounding axis and the four-quantity months_saved divergence)
- @app/services/debt_strategy.py
- @app/routes/loan.py (payoff/refinance arithmetic)
- @app/services/year_end_summary_service.py (mortgage interest aggregation, calendar-year
  view of total_interest)
- @app/services/loan_payment_service.py
- @app/utils/money.py (Commit 1; replaces bare .quantize(Decimal("0.01")) banker's sites)
- @app/services/loan_resolver.py (Commit 13)

Objective: every per-period principal/interest, total_interest, interest_saved,
months_saved, and payoff_date comes from loan_resolver.resolve_loan(...) -- one definition
each. Calendar-year and strategy-base views become explicit, labeled subsets of the one
life-of-loan schedule, not parallel computations. Every bare .quantize(Decimal("0.01"))
in these files is replaced with round_money (with hand-computed cents proof per site).

Production files this commit touches (re-grep all line numbers):
- app/services/debt_strategy.py
- app/routes/loan.py
- app/services/year_end_summary_service.py
- app/services/loan_payment_service.py
- tests in test_services and test_routes for the above

Apply these rules (the plan's Section 1 is the authoritative version):
1. The plan's specification for this commit (Section 9, subsections A through H) is the
   floor, not the ceiling. If verification surfaces extra in-scope refinements, fold them
   in and explain in the work summary.
2. Trust-but-verify: re-grep every cited symbol; read every file you will change in full;
   confirm the audit/plan claim still holds against current code before editing. If reality
   has drifted, stop and report in the work summary before continuing.
3. No shortcuts, no band-aid fixes. Fix root causes. Decimal money from strings, IDs and
   semantic booleans for business logic (never name strings), DRY/SOLID, fully normalized
   schema, pythonic type-hinted code, specific exceptions, substantive docstrings, no
   Unicode em/en dashes (use -- or -).
4. Never modify a test to make it pass except for the documented exception in plan Section 1
   rule 2: tests pinning a shipping wrong number this finding proves wrong. Every re-pinned
   assertion gets a comment naming the finding ID and the hand-computed arithmetic. List
   each in the work summary.
5. Targeted pytest during edits; pylint app/ --fail-on=E,F clean (no new warnings vs
   baseline); full pytest (-n 12 default) green as the per-commit final gate. Migrations
   (if any) round-trip upgrade->downgrade->upgrade cleanly; destructive changes get a Review
   docstring line and explicit developer approval before authoring.
6. Stay in scope. Out-of-scope issues, gold-plating opportunities, or refactors you noticed but did not
   perform MUST be flagged in the work summary with file:line and a one-sentence reason for not acting.
   Do not silently fold them in. Items that are not directly handled by a future commit need to be
   added to docs/audits/financial_calculations/remediation_follow_up.md. Any trivial items offer to
   address as a separate commit.
7. Do not push. After the work is green, present the work summary and ASK whether to commit
   and push to dev (this triggers CI; PR-to-main is required for promotion).

Re-pinned tests: any assertion whose value reflected the banker's-rounding half-cent or
the wrong months_saved quantity becomes the round_money / resolver value, with the
finding ID and arithmetic.

Work summary format (use these labels verbatim):
A. Verification: claims you re-verified; any drift found.
B. Files changed: list with one-line purpose each.
C. Tests added/modified: ID, name, hand-computed Decimal + arithmetic comment.
D. Re-pinned tests: finding ID + arithmetic per assertion, or "none".
E. Targeted-suite and full-suite final summary lines, verbatim from pytest.
F. Pylint final line, verbatim.
G. Migrations: upgrade->downgrade->upgrade result, or "n/a".
H. Invariants: which existing regression locks stayed green; which new locks added.
I. Discovered refinements folded in: each with rationale.
J. OUT OF SCOPE -- flagged, not fixed: file:line + reason per item + F-# entry in remediation_follow_up.md.
K. Open questions / assumptions made.
L. Proposed commit message (format: <type>(<scope>): <what> with the Co-Authored-By trailer
   per CLAUDE.md).
M. Ask: "Ready to commit and push to dev?"

Specific verification gates for this commit:
- Cross-surface equality: per-period principal/interest at each schedule index identical
  across debt_strategy / loan / year_end_summary.
- Hand-computed interest_saved with extra payment matches round_money output (and differs
  from the pre-fix banker's value by the documented half-cent).
- `grep -rn "\.quantize(Decimal(\"0\.01\"))" app/services/debt_strategy.py app/routes/
  loan.py app/services/year_end_summary_service.py app/services/loan_payment_service.py`
  is empty after this commit (all routed through round_money).
- ARM payoff_date identical across all surfaces.

If anything is unclear, ASK. If you encounter a quantize site whose current behavior
(banker's) cannot be shown to be wrong against a hand-computed expectation, STOP and
report -- migrating it could introduce a one-cent change that needs the developer's
explicit confirmation.
```

## Group D -- Independent criticals (CRIT-03, HIGH-03, CRIT-04, CRIT-05)

### Commit 18 -- `fix(tax): enforce SS wage-base cap on calibration path (CRIT-03)`

**Prereqs on dev:** none. **Closes:** CRIT-03 / F-037 (FICA Social-Security cap bypassed on
calibration path).

```text
You are executing Commit 18 of the Shekel financial-calculation audit remediation in a
fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/audits/financial_calculations/remediation_plan.md (Sections 0-7; Section 9 "Commit
  18" A-H; Section 11 hand-computed reconciliation for CRIT-03: +$7,905/yr overstatement
  on $312k salary)
- @CLAUDE.md, @docs/coding-standards.md, @docs/testing-standards.md
- @docs/audits/financial_calculations/08_findings.md (CRIT-03)
- @docs/audits/financial_calculations/03_consistency.md (F-037 details)
- @app/services/calibration_service.py (apply_calibration has no cumulative_wages
  parameter and no ss_wage_base reference -- the bug)
- @app/services/tax_calculator.py (calculate_fica enforces the cap correctly; this is the
  reference behavior the calibration path must match)
- @app/services/paycheck_calculator.py (the gate that routes between calibration and
  bracket; the bracket branch already computes cumulative_wages -- pass the same value
  into apply_calibration)

Objective: extract a single capped-SS helper (e.g. tax_calculator.capped_social_security(
gross, cumulative, fica_config) -> Decimal) used by both calculate_fica and
apply_calibration so the two paths cannot drift again. Thread cumulative_wages into
apply_calibration. The calibration path keeps its calibrated federal/state/medicare
effective rates; only the SS line uses the capped figure. After this commit, the year-
total SS for a high earner matches between calibration and bracket paths to the cent.

Production files this commit touches:
- app/services/tax_calculator.py (new capped_social_security helper)
- app/services/calibration_service.py (apply_calibration signature + SS line)
- app/services/paycheck_calculator.py (gate passes cumulative_wages into calibration)
- tests/test_services/test_calibration_service.py
- tests/test_services/test_paycheck_calculator.py
- tests/test_services/test_tax_calculator.py (capped_social_security helper tests)

Apply these rules (the plan's Section 1 is the authoritative version):
1. The plan's specification for this commit (Section 9, subsections A through H) is the
   floor, not the ceiling. If verification surfaces extra in-scope refinements, fold them
   in and explain in the work summary.
2. Trust-but-verify: re-grep every cited symbol; read every file you will change in full;
   confirm the audit/plan claim still holds against current code before editing. If reality
   has drifted, stop and report in the work summary before continuing.
3. No shortcuts, no band-aid fixes. Fix root causes. Decimal money from strings, IDs and
   semantic booleans for business logic (never name strings), DRY/SOLID, fully normalized
   schema, pythonic type-hinted code, specific exceptions, substantive docstrings, no
   Unicode em/en dashes (use -- or -).
4. Never modify a test to make it pass except for the documented exception in plan Section 1
   rule 2: tests pinning a shipping wrong number this finding proves wrong. Every re-pinned
   assertion gets a comment naming the finding ID and the hand-computed arithmetic. List
   each in the work summary.
5. Targeted pytest during edits; pylint app/ --fail-on=E,F clean (no new warnings vs
   baseline); full pytest (-n 12 default) green as the per-commit final gate. Migrations
   (if any) round-trip upgrade->downgrade->upgrade cleanly; destructive changes get a Review
   docstring line and explicit developer approval before authoring.
6. Stay in scope. Out-of-scope issues, gold-plating opportunities, or refactors you noticed but did not
   perform MUST be flagged in the work summary with file:line and a one-sentence reason for not acting.
   Do not silently fold them in. Items that are not directly handled by a future commit need to be
   added to docs/audits/financial_calculations/remediation_follow_up.md. Any trivial items offer to
   address as a separate commit.
7. Do not push. After the work is green, present the work summary and ASK whether to commit
   and push to dev (this triggers CI; PR-to-main is required for promotion).

Re-pinned tests: any calibration test that asserted the uncapped year SS becomes the
capped value with the IRS-invariant citation (CRIT-03 / F-037) and the arithmetic.

Work summary format (use these labels verbatim):
A. Verification: claims you re-verified; any drift found.
B. Files changed: list with one-line purpose each.
C. Tests added/modified: ID, name, hand-computed Decimal + arithmetic comment.
D. Re-pinned tests: finding ID + arithmetic per assertion, or "none".
E. Targeted-suite and full-suite final summary lines, verbatim from pytest.
F. Pylint final line, verbatim.
G. Migrations: upgrade->downgrade->upgrade result, or "n/a".
H. Invariants: which existing regression locks stayed green; which new locks added.
I. Discovered refinements folded in: each with rationale.
J. OUT OF SCOPE -- flagged, not fixed: file:line + reason per item + F-# entry in remediation_follow_up.md.
K. Open questions / assumptions made.
L. Proposed commit message (format: <type>(<scope>): <what> with the Co-Authored-By trailer
   per CLAUDE.md).
M. Ask: "Ready to commit and push to dev?"

Specific verification gates for this commit:
- Hand-computed cap test: $312k salary, 26 periods of $12,000 gross. SS goes to zero
  once cumulative >= ss_wage_base; partial in the crossing period (ss_taxable =
  ss_wage_base - cumulative). Year total == bracket year total exactly.
- Low-earner test: $60k salary; both paths return identical SS for every period (no
  regression).
- `grep -n "ss_wage_base\|cumulative_wages" app/services/calibration_service.py` is
  non-empty (the cap is now referenced).
- Single source of truth: `grep -rn "ss_wage_base\|ss_taxable" app/services/ | grep -v
  capped_social_security` shows no parallel cap arithmetic.

If anything is unclear, ASK. Confirm the FICA config object's exact field name for the
SS wage base before referencing it (read fica_config or whatever the bracket path passes
through).
```

---

### Commit 19 -- `fix(calibration): server-derive effective rates at confirm (E-20)`

**Prereqs on dev:** 18. **Closes:** HIGH-03 / Q-25 (E-20 immutable pay-stub snapshot).

```text
You are executing Commit 19 of the Shekel financial-calculation audit remediation in a
fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/audits/financial_calculations/remediation_plan.md (Sections 0-7; Section 9 "Commit
  19" A-H)
- @CLAUDE.md, @docs/coding-standards.md, @docs/testing-standards.md
- @docs/audits/financial_calculations/08_findings.md (HIGH-03)
- @docs/audits/financial_calculations/04_source_of_truth.md (effective_*_rate columns
  Q-25)
- @app/services/calibration_service.py (derive_effective_rates already exists; the
  confirm route must call it server-side instead of trusting posted fields)
- @app/routes/salary.py (calibrate_preview, calibrate_confirm)
- @app/schemas/validation.py (existing calibration schemas; add the cross-check)

Objective: calibrate_confirm re-derives the four effective rates server-side from the
stored actual_* and the taxable base via calibration_service.derive_effective_rates(),
ignoring any posted rate fields. The schema cross-checks that the stored rate pair is
consistent with the stored actual_* pair within a one-cent tolerance (rejecting tampered
or stale posts as 422 instead of silently storing them). Calibration becomes an immutable
pay-stub-grounded snapshot (E-20): editing pre-tax deductions or salary afterward does
not silently mutate stored rates.

Production files this commit touches:
- app/routes/salary.py (calibrate_confirm body)
- app/schemas/validation.py (calibration confirm schema cross-check)
- tests/test_routes/test_salary.py
- tests/test_services/test_calibration_service.py

Apply these rules (the plan's Section 1 is the authoritative version):
1. The plan's specification for this commit (Section 9, subsections A through H) is the
   floor, not the ceiling. If verification surfaces extra in-scope refinements, fold them
   in and explain in the work summary.
2. Trust-but-verify: re-grep every cited symbol; read every file you will change in full;
   confirm the audit/plan claim still holds against current code before editing. If reality
   has drifted, stop and report in the work summary before continuing.
3. No shortcuts, no band-aid fixes. Fix root causes. Decimal money from strings, IDs and
   semantic booleans for business logic (never name strings), DRY/SOLID, fully normalized
   schema, pythonic type-hinted code, specific exceptions, substantive docstrings, no
   Unicode em/en dashes (use -- or -).
4. Never modify a test to make it pass except for the documented exception in plan Section 1
   rule 2: tests pinning a shipping wrong number this finding proves wrong. Every re-pinned
   assertion gets a comment naming the finding ID and the hand-computed arithmetic. List
   each in the work summary.
5. Targeted pytest during edits; pylint app/ --fail-on=E,F clean (no new warnings vs
   baseline); full pytest (-n 12 default) green as the per-commit final gate. Migrations
   (if any) round-trip upgrade->downgrade->upgrade cleanly; destructive changes get a Review
   docstring line and explicit developer approval before authoring.
6. Stay in scope. Out-of-scope issues, gold-plating opportunities, or refactors you noticed but did not
   perform MUST be flagged in the work summary with file:line and a one-sentence reason for not acting.
   Do not silently fold them in. Items that are not directly handled by a future commit need to be
   added to docs/audits/financial_calculations/remediation_follow_up.md. Any trivial items offer to
   address as a separate commit.
7. Do not push. After the work is green, present the work summary and ASK whether to commit
   and push to dev (this triggers CI; PR-to-main is required for promotion).

Re-pinned tests: any assertion that trusted posted rate fields becomes the server-derived
value with HIGH-03 / Q-25 citations.

Work summary format (use these labels verbatim):
A. Verification: claims you re-verified; any drift found.
B. Files changed: list with one-line purpose each.
C. Tests added/modified: ID, name, hand-computed Decimal + arithmetic comment.
D. Re-pinned tests: finding ID + arithmetic per assertion, or "none".
E. Targeted-suite and full-suite final summary lines, verbatim from pytest.
F. Pylint final line, verbatim.
G. Migrations: upgrade->downgrade->upgrade result, or "n/a".
H. Invariants: which existing regression locks stayed green; which new locks added.
I. Discovered refinements folded in: each with rationale.
J. OUT OF SCOPE -- flagged, not fixed: file:line + reason per item + F-# entry in remediation_follow_up.md.
K. Open questions / assumptions made.
L. Proposed commit message (format: <type>(<scope>): <what> with the Co-Authored-By trailer
   per CLAUDE.md).
M. Ask: "Ready to commit and push to dev?"

Specific verification gates for this commit:
- POST with rate fields that differ from server-derived: server stores derived (or 422 on
  inconsistency, depending on the chosen contract -- prefer 422 if the inconsistency
  exceeds the one-cent tolerance, since posted-but-wrong fields are a signal of tampering
  or staleness).
- Edit deductions after calibrating -> stored snapshot unchanged.
- Capped SS from Commit 18 flows through the calibrated paycheck (cross-cut test).

If anything is unclear, ASK. The contract decision -- "server-derives silently and
ignores posted" vs "server-derives and returns 422 on disagreement" -- matters; pick the
stricter (422) since CLAUDE.md rule 4 says never ignore a problem, and a discrepancy is
a problem.
```

---

### Commit 20 -- `fix(retirement): zero is a value not missing (E-12, CRIT-04)`

**Prereqs on dev:** none. **Closes:** CRIT-04 / F-042 / PA-04 / PA-05 (E-12 zero-not-missing
semantics).

```text
You are executing Commit 20 of the Shekel financial-calculation audit remediation in a
fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/audits/financial_calculations/remediation_plan.md (Sections 0-7; Section 9 "Commit
  20" A-H; Section 11 reconciliation for CRIT-04: phantom $4,000/mo on $1.2M with swr=0;
  3.50% vs 7.00% blended return)
- @CLAUDE.md, @docs/coding-standards.md
- @docs/audits/financial_calculations/08_findings.md (CRIT-04)
- @app/services/retirement_dashboard_service.py (the file being fixed; locate the `or
  "0.04"` truthiness and the `if params and params.assumed_annual_return:` truthiness)
- @app/services/retirement_gap_calculator.py
- @app/models/user.py (UserSettings.safe_withdrawal_rate)
- @app/models/investment_params.py (InvestmentParams.assumed_annual_return)

Objective: introduce one SWR resolver helper used by both compute_gap_data and
compute_slider_defaults (replacing the truthiness `or "0.04"` AND the parallel `is None`
slider semantics with one definition). Replace the weighted-return loop's `if params and
params.assumed_annual_return:` with `params is not None and params.assumed_annual_return
is not None` so a zero-return account contributes (and a None one is skipped). Use the
existing named constant _DEFAULT_SWR_PCT consistently; never an inline literal. Apply the
broader project rule that zero monetary/rate values are real values and only None means
"missing" (E-12 / coding standard "do not rely on truthiness for business logic").

Production files this commit touches:
- app/services/retirement_dashboard_service.py
- tests/test_services/test_retirement_dashboard_service.py
- tests/test_services/test_retirement_gap_calculator.py

Apply these rules (the plan's Section 1 is the authoritative version):
1. The plan's specification for this commit (Section 9, subsections A through H) is the
   floor, not the ceiling. If verification surfaces extra in-scope refinements, fold them
   in and explain in the work summary.
2. Trust-but-verify: re-grep every cited symbol; read every file you will change in full;
   confirm the audit/plan claim still holds against current code before editing. If reality
   has drifted, stop and report in the work summary before continuing.
3. No shortcuts, no band-aid fixes. Fix root causes. Decimal money from strings, IDs and
   semantic booleans for business logic (never name strings), DRY/SOLID, fully normalized
   schema, pythonic type-hinted code, specific exceptions, substantive docstrings, no
   Unicode em/en dashes (use -- or -).
4. Never modify a test to make it pass except for the documented exception in plan Section 1
   rule 2: tests pinning a shipping wrong number this finding proves wrong. Every re-pinned
   assertion gets a comment naming the finding ID and the hand-computed arithmetic. List
   each in the work summary.
5. Targeted pytest during edits; pylint app/ --fail-on=E,F clean (no new warnings vs
   baseline); full pytest (-n 12 default) green as the per-commit final gate. Migrations
   (if any) round-trip upgrade->downgrade->upgrade cleanly; destructive changes get a Review
   docstring line and explicit developer approval before authoring.
6. Stay in scope. Out-of-scope issues, gold-plating opportunities, or refactors you noticed but did not
   perform MUST be flagged in the work summary with file:line and a one-sentence reason for not acting.
   Do not silently fold them in. Items that are not directly handled by a future commit need to be
   added to docs/audits/financial_calculations/remediation_follow_up.md. Any trivial items offer to
   address as a separate commit.
7. Do not push. After the work is green, present the work summary and ASK whether to commit
   and push to dev (this triggers CI; PR-to-main is required for promotion).

Re-pinned tests: phantom-income and weighted-return assertions with CRIT-04 / F-042 /
PA-04 / PA-05 citations and the arithmetic.

Work summary format (use these labels verbatim):
A. Verification: claims you re-verified; any drift found.
B. Files changed: list with one-line purpose each.
C. Tests added/modified: ID, name, hand-computed Decimal + arithmetic comment.
D. Re-pinned tests: finding ID + arithmetic per assertion, or "none".
E. Targeted-suite and full-suite final summary lines, verbatim from pytest.
F. Pylint final line, verbatim.
G. Migrations: upgrade->downgrade->upgrade result, or "n/a".
H. Invariants: which existing regression locks stayed green; which new locks added.
I. Discovered refinements folded in: each with rationale.
J. OUT OF SCOPE -- flagged, not fixed: file:line + reason per item + F-# entry in remediation_follow_up.md.
K. Open questions / assumptions made.
L. Proposed commit message (format: <type>(<scope>): <what> with the Co-Authored-By trailer
   per CLAUDE.md).
M. Ask: "Ready to commit and push to dev?"

Specific verification gates for this commit:
- Explicit zero SWR: slider shows 0.00% AND projected income shows $0.00 (consistent --
  the phantom $4,000 is gone).
- Two $100k accounts at 0% and 7%: blended return is 3.50% (hand-computed).
- One None return + one 0% return: None excluded, 0% included.
- `grep -n 'or "0\.04"\|or 0\.04' app/services/retirement_dashboard_service.py` is empty.
- `grep -n 'and params\.assumed_annual_return:\|and .*safe_withdrawal_rate:' app/
  services/retirement_dashboard_service.py` is empty (no truthiness on financial values).

If anything is unclear, ASK.
```

---

### Commit 21 -- `fix(templates): semantic is_settled hard-delete guard (E-22, CRIT-05)`

**Prereqs on dev:** none. **Closes:** CRIT-05 (E-22 -- irreversible RECEIVED-history data loss).

```text
You are executing Commit 21 of the Shekel financial-calculation audit remediation in a
fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/audits/financial_calculations/remediation_plan.md (Sections 0-7; Section 9 "Commit
  21" A-H)
- @CLAUDE.md, @docs/coding-standards.md, @docs/testing-standards.md
- @docs/audits/financial_calculations/08_findings.md (CRIT-05)
- @app/utils/archive_helpers.py (template_has_paid_history and
  transfer_template_has_paid_history -- both enumerate [DONE, SETTLED] and OMIT RECEIVED)
- @app/routes/templates.py (hard_delete_template -- the unconditional bulk delete and the
  "permanently deleted" flash)
- @app/ref_seeds.py (Status rows; RECEIVED is_settled=True, is_immutable=True -- identical
  protection to DONE/SETTLED)
- @app/routes/transactions.py (mark-done sets income to RECEIVED -- the path that creates
  the data that the bug then destroys)

Objective: replace the enumerated [DONE, SETTLED] status-ID predicate in both archive
helpers with the semantic Status.is_settled.is_(True) ORM filter -- this automatically
protects RECEIVED and any future settled status (root cause, not band-aid). Additionally
constrain hard_delete_template's bulk delete to non-settled rows so even if a guard is
bypassed, settled rows survive (defense in depth for an irreversible operation). Fix the
"permanently deleted" flash message to be accurate when archive-fallback was actually
taken.

Production files this commit touches:
- app/utils/archive_helpers.py (both predicates -> Status.is_settled)
- app/routes/templates.py (hard_delete_template restrict-to-non-settled + accurate flash)
- tests/test_utils/test_archive_helpers.py
- tests/test_routes/test_templates.py

Apply these rules (the plan's Section 1 is the authoritative version):
1. The plan's specification for this commit (Section 9, subsections A through H) is the
   floor, not the ceiling. If verification surfaces extra in-scope refinements, fold them
   in and explain in the work summary.
2. Trust-but-verify: re-grep every cited symbol; read every file you will change in full;
   confirm the audit/plan claim still holds against current code before editing. If reality
   has drifted, stop and report in the work summary before continuing.
3. No shortcuts, no band-aid fixes. Fix root causes. Decimal money from strings, IDs and
   semantic booleans for business logic (never name strings), DRY/SOLID, fully normalized
   schema, pythonic type-hinted code, specific exceptions, substantive docstrings, no
   Unicode em/en dashes (use -- or -).
4. Never modify a test to make it pass except for the documented exception in plan Section 1
   rule 2: tests pinning a shipping wrong number this finding proves wrong. Every re-pinned
   assertion gets a comment naming the finding ID and the hand-computed arithmetic. List
   each in the work summary.
5. Targeted pytest during edits; pylint app/ --fail-on=E,F clean (no new warnings vs
   baseline); full pytest (-n 12 default) green as the per-commit final gate. Migrations
   (if any) round-trip upgrade->downgrade->upgrade cleanly; destructive changes get a Review
   docstring line and explicit developer approval before authoring.
6. Stay in scope. Out-of-scope issues, gold-plating opportunities, or refactors you noticed but did not
   perform MUST be flagged in the work summary with file:line and a one-sentence reason for not acting.
   Do not silently fold them in. Items that are not directly handled by a future commit need to be
   added to docs/audits/financial_calculations/remediation_follow_up.md. Any trivial items offer to
   address as a separate commit.
7. Do not push. After the work is green, present the work summary and ASK whether to commit
   and push to dev (this triggers CI; PR-to-main is required for promotion).

Work summary format (use these labels verbatim):
A. Verification: claims you re-verified; any drift found.
B. Files changed: list with one-line purpose each.
C. Tests added/modified: ID, name, hand-computed Decimal + arithmetic comment.
D. Re-pinned tests: finding ID + arithmetic per assertion, or "none".
E. Targeted-suite and full-suite final summary lines, verbatim from pytest.
F. Pylint final line, verbatim.
G. Migrations: upgrade->downgrade->upgrade result, or "n/a".
H. Invariants: which existing regression locks stayed green; which new locks added.
I. Discovered refinements folded in: each with rationale.
J. OUT OF SCOPE -- flagged, not fixed: file:line + reason per item + F-# entry in remediation_follow_up.md.
K. Open questions / assumptions made.
L. Proposed commit message (format: <type>(<scope>): <what> with the Co-Authored-By trailer
   per CLAUDE.md).
M. Ask: "Ready to commit and push to dev?"

Specific verification gates for this commit (this is data-loss territory; assertions are
non-negotiable):
- Income template with one RECEIVED txn: hard_delete BLOCKED, template archived, RECEIVED
  txn intact in the DB after the action.
- Income template with one SETTLED txn: hard_delete BLOCKED (regression check).
- Income template with one DONE txn: hard_delete BLOCKED (regression check).
- Projected-only template: hard_delete proceeds (intended).
- Mixed Projected + RECEIVED: even if the path is entered, the bulk delete skips settled
  rows (assert post-condition that settled rows survive).
- Transfer-template variant: RECEIVED shadow -> hard_delete blocked.
- `grep -n "\[paid_id, settled_id\]\|\[DONE_ID, SETTLED_ID\]" app/utils/archive_helpers.py`
  is empty.
- Flash message text is accurate (no "permanently deleted" when archive-fallback was
  taken).

If anything is unclear, ASK. This commit closes the irreversible-data-loss path; pinning
every settled-status case is non-negotiable.
```

---

### Commit 22 -- `chore(audit): read-only scan for pre-fix destroyed RECEIVED history (OPT-2)`

**Prereqs on dev:** 21. **Closes:** OPT-2 (optional integrity diagnostic; informs whether
reconstruction is warranted).

```text
You are executing Commit 22 of the Shekel financial-calculation audit remediation in a
fresh session. This commit is OPTIONAL per the plan's Section 5 (listed as OPT-2). The
developer has opted in by running this prompt. Work in the project root on dev.

Required reading -- in full:
- @docs/audits/financial_calculations/remediation_plan.md (Sections 0-7; Section 5 OPT-2;
  Section 9 "Commit 22" A-H)
- @CLAUDE.md (Audit Triggers section -- system.audit_log is the tamper-resistant forensic
  source this scan reads from)
- @docs/coding-standards.md (Shell Scripts section)
- @app/audit_infrastructure.py (AUDITED_TABLES; trigger function semantics)
- existing scripts in scripts/ for the project conventions (--force, idempotency, never
  print secrets)

Objective: create scripts/scan_destroyed_received_history.py as a strictly read-only
diagnostic that cross-references system.audit_log DELETE rows on budget.transactions
against the template hard-delete path before Commit 21. It reports affected templates,
periods, amounts, and timestamps. It NEVER mutates the database. It produces a report
that informs whether manual data reconstruction is warranted.

Production files this commit touches:
- scripts/scan_destroyed_received_history.py (new; read-only)
- tests/test_scripts/test_scan_destroyed_received_history.py (new)

Apply these rules (the plan's Section 1 is the authoritative version):
1. The plan's specification for this commit (Section 9, subsections A through H) is the
   floor, not the ceiling. If verification surfaces extra in-scope refinements, fold them
   in and explain in the work summary.
2. Trust-but-verify: re-grep every cited symbol; read every file you will change in full;
   confirm the audit/plan claim still holds against current code before editing. If reality
   has drifted, stop and report in the work summary before continuing.
3. No shortcuts, no band-aid fixes. Fix root causes. Decimal money from strings, IDs and
   semantic booleans for business logic (never name strings), DRY/SOLID, fully normalized
   schema, pythonic type-hinted code, specific exceptions, substantive docstrings, no
   Unicode em/en dashes (use -- or -).
4. Never modify a test to make it pass except for the documented exception in plan Section 1
   rule 2: tests pinning a shipping wrong number this finding proves wrong. Every re-pinned
   assertion gets a comment naming the finding ID and the hand-computed arithmetic. List
   each in the work summary.
5. Targeted pytest during edits; pylint app/ --fail-on=E,F clean (no new warnings vs
   baseline); full pytest (-n 12 default) green as the per-commit final gate. Migrations
   (if any) round-trip upgrade->downgrade->upgrade cleanly; destructive changes get a Review
   docstring line and explicit developer approval before authoring.
6. Stay in scope. Out-of-scope issues, gold-plating opportunities, or refactors you noticed but did not
   perform MUST be flagged in the work summary with file:line and a one-sentence reason for not acting.
   Do not silently fold them in. Items that are not directly handled by a future commit need to be
   added to docs/audits/financial_calculations/remediation_follow_up.md. Any trivial items offer to
   address as a separate commit.
7. Do not push. After the work is green, present the work summary and ASK whether to commit
   and push to dev (this triggers CI; PR-to-main is required for promotion).

Script conventions (coding standard, Shell Scripts):
- Validate inputs; fail with a clear message rather than silent defaults.
- Idempotent: running it twice produces the same report.
- Never print secrets; use the [set via environment variable] convention if env vars are
  referenced.
- No --force flag (this script is read-only; --force is not applicable).
- Type hints, docstrings, specific exceptions, pylint compliance -- this is production
  code by the standard's wording.

Work summary format (use these labels verbatim):
A. Verification: claims you re-verified; any drift found.
B. Files changed: list with one-line purpose each.
C. Tests added/modified: ID, name, hand-computed Decimal + arithmetic comment.
D. Re-pinned tests: finding ID + arithmetic per assertion, or "none".
E. Targeted-suite and full-suite final summary lines, verbatim from pytest.
F. Pylint final line, verbatim.
G. Migrations: upgrade->downgrade->upgrade result, or "n/a".
H. Invariants: which existing regression locks stayed green; which new locks added.
I. Discovered refinements folded in: each with rationale.
J. OUT OF SCOPE -- flagged, not fixed: file:line + reason per item + F-# entry in remediation_follow_up.md.
K. Open questions / assumptions made.
L. Proposed commit message (format: <type>(<scope>): <what> with the Co-Authored-By trailer
   per CLAUDE.md).
M. Ask: "Ready to commit and push to dev?"

Specific verification gates for this commit:
- A test seeds an audit_log DELETE row that the post-Commit-21 guard would block; the
  scan reports it.
- A read-only invariant: snapshot system.audit_log row count before and after running
  the scan; identical (script writes nothing).
- Clean DB scan: empty report, exit 0.

If anything is unclear, ASK. The audit_log schema and trigger semantics are project-
specific -- read app/audit_infrastructure.py before writing any SQL.
```

---

## Group E -- HIGH structural / source-of-truth / standards (23-27)

### Commit 23 -- `refactor(obligations): one monthly-equivalent aggregator (E-24, HIGH-05)`

**Prereqs on dev:** none structurally, but Commit 1's named constants pattern is the model.
**Closes:** HIGH-05 / D6-05 (E-24 -- expired-template overcount + 26/12 redeclared).

```text
You are executing Commit 23 of the Shekel financial-calculation audit remediation in a
fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/audits/financial_calculations/remediation_plan.md (Sections 0-7; Section 9 "Commit
  23" A-H)
- @CLAUDE.md, @docs/coding-standards.md, @docs/testing-standards.md
- @docs/audits/financial_calculations/08_findings.md (HIGH-05)
- @docs/audits/financial_calculations/06_dry_solid.md (D6-05 with the four-loop + missing-
  end_date-filter details)
- @app/services/savings_goal_service.py (compute_committed_monthly lacks the
  rule.end_date < today guard; PAY_PERIODS_PER_YEAR exists at the top, inlined elsewhere)
- @app/routes/obligations.py (three near-identical loops that DO skip expired)
- @app/services/savings_dashboard_service.py (26/12 inlined at two sites)
- @app/services/retirement_gap_calculator.py (26/12 inlined)

Objective: create one canonical monthly-equivalent aggregator (e.g.
app/services/obligations_aggregator.py exposing committed_monthly(user_id, scenario_id,
as_of)) with the shared filter: skip ONCE-pattern rules, skip rule.end_date is not None
and rule.end_date < as_of. Route the three /obligations loops and
savings_goal_service.compute_committed_monthly through it. Define PAY_PERIODS_PER_YEAR =
Decimal("26") and MONTHS_PER_YEAR = Decimal("12") in one module (Commit 1 introduced
money.py; either co-locate or create a constants module); every 26/12 site imports them.

Production files this commit touches:
- app/services/obligations_aggregator.py (new) or extension of an existing service
- app/routes/obligations.py
- app/services/savings_goal_service.py
- app/services/savings_dashboard_service.py
- app/services/retirement_gap_calculator.py
- (constants module if one is added)
- tests for each

Apply these rules (the plan's Section 1 is the authoritative version):
1. The plan's specification for this commit (Section 9, subsections A through H) is the
   floor, not the ceiling. If verification surfaces extra in-scope refinements, fold them
   in and explain in the work summary.
2. Trust-but-verify: re-grep every cited symbol; read every file you will change in full;
   confirm the audit/plan claim still holds against current code before editing. If reality
   has drifted, stop and report in the work summary before continuing.
3. No shortcuts, no band-aid fixes. Fix root causes. Decimal money from strings, IDs and
   semantic booleans for business logic (never name strings), DRY/SOLID, fully normalized
   schema, pythonic type-hinted code, specific exceptions, substantive docstrings, no
   Unicode em/en dashes (use -- or -).
4. Never modify a test to make it pass except for the documented exception in plan Section 1
   rule 2: tests pinning a shipping wrong number this finding proves wrong. Every re-pinned
   assertion gets a comment naming the finding ID and the hand-computed arithmetic. List
   each in the work summary.
5. Targeted pytest during edits; pylint app/ --fail-on=E,F clean (no new warnings vs
   baseline); full pytest (-n 12 default) green as the per-commit final gate. Migrations
   (if any) round-trip upgrade->downgrade->upgrade cleanly; destructive changes get a Review
   docstring line and explicit developer approval before authoring.
6. Stay in scope. Out-of-scope issues, gold-plating opportunities, or refactors you noticed but did not
   perform MUST be flagged in the work summary with file:line and a one-sentence reason for not acting.
   Do not silently fold them in. Items that are not directly handled by a future commit need to be
   added to docs/audits/financial_calculations/remediation_follow_up.md. Any trivial items offer to
   address as a separate commit.
7. Do not push. After the work is green, present the work summary and ASK whether to commit
   and push to dev (this triggers CI; PR-to-main is required for promotion).

Re-pinned tests: emergency-fund baseline and per-goal contribution-floor assertions that
were inflated by expired templates become the corrected hand-computed values with HIGH-05
/ D6-05 citations.

Work summary format (use these labels verbatim):
A. Verification: claims you re-verified; any drift found.
B. Files changed: list with one-line purpose each.
C. Tests added/modified: ID, name, hand-computed Decimal + arithmetic comment.
D. Re-pinned tests: finding ID + arithmetic per assertion, or "none".
E. Targeted-suite and full-suite final summary lines, verbatim from pytest.
F. Pylint final line, verbatim.
G. Migrations: upgrade->downgrade->upgrade result, or "n/a".
H. Invariants: which existing regression locks stayed green; which new locks added.
I. Discovered refinements folded in: each with rationale.
J. OUT OF SCOPE -- flagged, not fixed: file:line + reason per item + F-# entry in remediation_follow_up.md.
K. Open questions / assumptions made.
L. Proposed commit message (format: <type>(<scope>): <what> with the Co-Authored-By trailer
   per CLAUDE.md).
M. Ask: "Ready to commit and push to dev?"

Specific verification gates for this commit:
- Expired recurring expense: excluded from compute_committed_monthly AND from /obligations
  (hand-computed).
- /obligations total and savings emergency-fund baseline reconcile to one number (same
  aggregator).
- `grep -rn "Decimal(\"26\")\|Decimal(\"12\")\| 26 / 12 \| 26\.0 / 12\.0" app/services/`
  shows only the constants module's definition (one source).
- ONCE pattern still counted exactly once.

If anything is unclear, ASK.
```

---

### Commit 24 -- `fix(schema): reconcile Marshmallow domains with DB CHECK (E-28, HIGH-06)`

**Prereqs on dev:** ideally 20 (the E-12 pattern is consistent with this commit's philosophy).
**Closes:** HIGH-06 / PA-01 / PA-02 (E-28 -- schema/DB CHECK divergence; silent rate defaults).

```text
You are executing Commit 24 of the Shekel financial-calculation audit remediation in a
fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/audits/financial_calculations/remediation_plan.md (Sections 0-7; Section 9 "Commit
  24" A-H)
- @CLAUDE.md
- @docs/coding-standards.md (especially the Migrations section -- CHECK constraint changes
  are destructive)
- @docs/testing-standards.md
- @docs/audits/financial_calculations/08_findings.md (HIGH-06)
- @docs/audits/financial_calculations/04_source_of_truth.md (Family C with the column-by-
  column UNCLEAR/CACHED analysis)
- @app/schemas/validation.py (the Marshmallow domains being reconciled)
- @app/models/user.py (UserSettings.trend_alert_threshold among others)
- @app/models/interest_params.py (apy with server_default 4.5% silent on first save)
- @app/models/investment_params.py (assumed_annual_return float default;
  annual_contribution_limit 0-vs-NULL three-way ambiguity)

Objective: align each Marshmallow domain with its DB CHECK and fix the three documented
defects: (a) trend_alert_threshold writable (Marshmallow Range vs DB CHECK must agree);
(b) rate fields use a consistent domain across schema/DB/model (fraction vs percentage
decided per column and documented); (c) apy first-save cannot silently inherit
server_default 4.5% (require explicit value or normalize zero/None per E-12); (d)
annual_contribution_limit zero-vs-NULL collapsed to one consistent meaning across the
three consumers; (e) assumed_annual_return default constructed from string Decimal,
not float literal. Migrations where CHECK changes are destructive: carry a Review:
docstring line; require working downgrade or NotImplementedError with the manual SQL.

Production files this commit touches (re-grep all):
- app/schemas/validation.py
- app/models/user.py (UserSettings)
- app/models/interest_params.py
- app/models/investment_params.py
- migrations/versions/<auto>_reconcile_check_domains.py (new)
- tests in test_schemas and test_models

Apply these rules (the plan's Section 1 is the authoritative version):
1. The plan's specification for this commit (Section 9, subsections A through H) is the
   floor, not the ceiling. If verification surfaces extra in-scope refinements, fold them
   in and explain in the work summary.
2. Trust-but-verify: re-grep every cited symbol; read every file you will change in full;
   confirm the audit/plan claim still holds against current code before editing. If reality
   has drifted, stop and report in the work summary before continuing.
3. No shortcuts, no band-aid fixes. Fix root causes. Decimal money from strings, IDs and
   semantic booleans for business logic (never name strings), DRY/SOLID, fully normalized
   schema, pythonic type-hinted code, specific exceptions, substantive docstrings, no
   Unicode em/en dashes (use -- or -).
4. Never modify a test to make it pass except for the documented exception in plan Section 1
   rule 2: tests pinning a shipping wrong number this finding proves wrong. Every re-pinned
   assertion gets a comment naming the finding ID and the hand-computed arithmetic. List
   each in the work summary.
5. Targeted pytest during edits; pylint app/ --fail-on=E,F clean (no new warnings vs
   baseline); full pytest (-n 12 default) green as the per-commit final gate. Migrations
   (if any) round-trip upgrade->downgrade->upgrade cleanly; destructive changes get a Review
   docstring line and explicit developer approval before authoring.
6. Stay in scope. Out-of-scope issues, gold-plating opportunities, or refactors you noticed but did not
   perform MUST be flagged in the work summary with file:line and a one-sentence reason for not acting.
   Do not silently fold them in. Items that are not directly handled by a future commit need to be
   added to docs/audits/financial_calculations/remediation_follow_up.md. Any trivial items offer to
   address as a separate commit.
7. Do not push. After the work is green, present the work summary and ASK whether to commit
   and push to dev (this triggers CI; PR-to-main is required for promotion).

Note for this commit (rule 5 emphasis -- migrations): any CHECK replacement is destructive;
do not author it without confirming the developer has read the migration and approves
before this commit lands. The migration carries a Review docstring line per the coding
standard.

Re-pinned tests: any assertion that depended on the silent server_default or the
mismatched domain becomes the corrected expectation with HIGH-06 / PA-01 / PA-02 citations.

Work summary format (use these labels verbatim):
A. Verification: claims you re-verified; any drift found.
B. Files changed: list with one-line purpose each.
C. Tests added/modified: ID, name, hand-computed Decimal + arithmetic comment.
D. Re-pinned tests: finding ID + arithmetic per assertion, or "none".
E. Targeted-suite and full-suite final summary lines, verbatim from pytest.
F. Pylint final line, verbatim.
G. Migrations: upgrade->downgrade->upgrade result, or "n/a".
H. Invariants: which existing regression locks stayed green; which new locks added.
I. Discovered refinements folded in: each with rationale.
J. OUT OF SCOPE -- flagged, not fixed: file:line + reason per item + F-# entry in remediation_follow_up.md.
K. Open questions / assumptions made.
L. Proposed commit message (format: <type>(<scope>): <what> with the Co-Authored-By trailer
   per CLAUDE.md).
M. Ask: "Ready to commit and push to dev?"

Specific verification gates for this commit:
- trend_alert_threshold round-trips through save+reload (was previously unwritable).
- First-save of an interest account omitting apy: explicit error or normalized value --
  no silent 4.5%.
- annual_contribution_limit == 0 produces ONE consistent behavior across the three
  consumers (the chosen meaning is documented in the model docstring and in the work
  summary, with citations).
- `grep -n "default=0\." app/models/investment_params.py` shows Decimal("...") form, no
  float literal.
- Migration upgrade -> downgrade -> upgrade clean.

If anything is unclear, ASK -- especially about the chosen meaning for
annual_contribution_limit == 0 across the three consumers. Pick one consistent meaning,
document it, and ask if you are unsure which the developer wants (do not silently choose).
```

---

### Commit 25 -- `fix(investment): unify employer-match across card/chart/year-end (HIGH-07)`

**Prereqs on dev:** none. **Closes:** HIGH-07 / F-043 / F-055 (card overstates match near limit).

```text
You are executing Commit 25 of the Shekel financial-calculation audit remediation in a
fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/audits/financial_calculations/remediation_plan.md (Sections 0-7; Section 9 "Commit
  25" A-H)
- @CLAUDE.md, @docs/coding-standards.md, @docs/testing-standards.md
- @docs/audits/financial_calculations/08_findings.md (HIGH-07)
- @docs/audits/financial_calculations/03_consistency.md (F-043, F-055 details with the
  card-$240 vs chart-$100 worked example)
- @app/services/growth_engine.py (calculate_employer_contribution is the sole producer --
  do not duplicate; just feed it the capped value)
- @app/routes/investment.py (the dashboard card call site uses the UNCAPPED periodic
  contribution; the chart and year-end use the limit-capped value)
- @app/services/year_end_summary_service.py (year_summary_employer_total)

Objective: at the dashboard card call site, pass the limit-capped contribution into
calculate_employer_contribution so the card matches the chart and year-end. The capping
logic already exists in the engine (~`:258-265`); the card must not bypass it. This is a
single-line fix conceptually; the value of this commit is the three-surface equality test.

Production files this commit touches:
- app/routes/investment.py (the card call site)
- tests/test_routes/test_investment.py
- tests/test_services/test_growth_engine.py
- tests/test_services/test_year_end_summary_service.py

Apply these rules (the plan's Section 1 is the authoritative version):
1. The plan's specification for this commit (Section 9, subsections A through H) is the
   floor, not the ceiling. If verification surfaces extra in-scope refinements, fold them
   in and explain in the work summary.
2. Trust-but-verify: re-grep every cited symbol; read every file you will change in full;
   confirm the audit/plan claim still holds against current code before editing. If reality
   has drifted, stop and report in the work summary before continuing.
3. No shortcuts, no band-aid fixes. Fix root causes. Decimal money from strings, IDs and
   semantic booleans for business logic (never name strings), DRY/SOLID, fully normalized
   schema, pythonic type-hinted code, specific exceptions, substantive docstrings, no
   Unicode em/en dashes (use -- or -).
4. Never modify a test to make it pass except for the documented exception in plan Section 1
   rule 2: tests pinning a shipping wrong number this finding proves wrong. Every re-pinned
   assertion gets a comment naming the finding ID and the hand-computed arithmetic. List
   each in the work summary.
5. Targeted pytest during edits; pylint app/ --fail-on=E,F clean (no new warnings vs
   baseline); full pytest (-n 12 default) green as the per-commit final gate. Migrations
   (if any) round-trip upgrade->downgrade->upgrade cleanly; destructive changes get a Review
   docstring line and explicit developer approval before authoring.
6. Stay in scope. Out-of-scope issues, gold-plating opportunities, or refactors you noticed but did not
   perform MUST be flagged in the work summary with file:line and a one-sentence reason for not acting.
   Do not silently fold them in. Items that are not directly handled by a future commit need to be
   added to docs/audits/financial_calculations/remediation_follow_up.md. Any trivial items offer to
   address as a separate commit.
7. Do not push. After the work is green, present the work summary and ASK whether to commit
   and push to dev (this triggers CI; PR-to-main is required for promotion).

Re-pinned tests: card employer-match assertions for limit-binding fixtures become the
capped value with HIGH-07 / F-043 / F-055 citations and the arithmetic.

Work summary format (use these labels verbatim):
A. Verification: claims you re-verified; any drift found.
B. Files changed: list with one-line purpose each.
C. Tests added/modified: ID, name, hand-computed Decimal + arithmetic comment.
D. Re-pinned tests: finding ID + arithmetic per assertion, or "none".
E. Targeted-suite and full-suite final summary lines, verbatim from pytest.
F. Pylint final line, verbatim.
G. Migrations: upgrade->downgrade->upgrade result, or "n/a".
H. Invariants: which existing regression locks stayed green; which new locks added.
I. Discovered refinements folded in: each with rationale.
J. OUT OF SCOPE -- flagged, not fixed: file:line + reason per item + F-# entry in remediation_follow_up.md.
K. Open questions / assumptions made.
L. Proposed commit message (format: <type>(<scope>): <what> with the Co-Authored-By trailer
   per CLAUDE.md).
M. Ask: "Ready to commit and push to dev?"

Specific verification gates for this commit:
- Account near limit: card per-period employer == chart employer line == year-end
  per-period sum, identical Decimal (the previously diverging $240 / $100 / $100 split
  collapses to one capped value).
- Account well below limit: card value unchanged from before this commit (no regression).
- Match-type employer at the boundary: all three surfaces show the capped value.

If anything is unclear, ASK.
```

---

### Commit 26 -- `fix(savings): DTI gross from raise-aware paycheck producer (MED-06)`

**Prereqs on dev:** none. **Closes:** MED-06 / F-032 (DTI denominator flat 26/12 vs raise-aware
paycheck engine).

```text
You are executing Commit 26 of the Shekel financial-calculation audit remediation in a
fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/audits/financial_calculations/remediation_plan.md (Sections 0-7; Section 9 "Commit
  26" A-H)
- @CLAUDE.md, @docs/coding-standards.md, @docs/testing-standards.md
- @docs/audits/financial_calculations/08_findings.md (MED-06)
- @docs/audits/financial_calculations/03_consistency.md (F-032 with the 26.9% vs 27.7%
  worked example)
- @app/services/savings_dashboard_service.py (DTI gross monthly calculation -- the flat
  biweekly * 26 / 12)
- @app/services/paycheck_calculator.py (the canonical raise-aware income producer)

Objective: DTI gross monthly income on /savings is sourced from the paycheck engine for
the period span (raise-applicable), not from a flat 26/12 conversion. One income
producer; remove the flat factor from the DTI path. Where a genuine flat conversion is
still appropriate elsewhere, use the named constants from Commit 23 with documentation
of why the flat path is correct there.

Production files this commit touches:
- app/services/savings_dashboard_service.py
- tests/test_services/test_savings_dashboard_service.py

Apply these rules (the plan's Section 1 is the authoritative version):
1. The plan's specification for this commit (Section 9, subsections A through H) is the
   floor, not the ceiling. If verification surfaces extra in-scope refinements, fold them
   in and explain in the work summary.
2. Trust-but-verify: re-grep every cited symbol; read every file you will change in full;
   confirm the audit/plan claim still holds against current code before editing. If reality
   has drifted, stop and report in the work summary before continuing.
3. No shortcuts, no band-aid fixes. Fix root causes. Decimal money from strings, IDs and
   semantic booleans for business logic (never name strings), DRY/SOLID, fully normalized
   schema, pythonic type-hinted code, specific exceptions, substantive docstrings, no
   Unicode em/en dashes (use -- or -).
4. Never modify a test to make it pass except for the documented exception in plan Section 1
   rule 2: tests pinning a shipping wrong number this finding proves wrong. Every re-pinned
   assertion gets a comment naming the finding ID and the hand-computed arithmetic. List
   each in the work summary.
5. Targeted pytest during edits; pylint app/ --fail-on=E,F clean (no new warnings vs
   baseline); full pytest (-n 12 default) green as the per-commit final gate. Migrations
   (if any) round-trip upgrade->downgrade->upgrade cleanly; destructive changes get a Review
   docstring line and explicit developer approval before authoring.
6. Stay in scope. Out-of-scope issues, gold-plating opportunities, or refactors you noticed but did not
   perform MUST be flagged in the work summary with file:line and a one-sentence reason for not acting.
   Do not silently fold them in. Items that are not directly handled by a future commit need to be
   added to docs/audits/financial_calculations/remediation_follow_up.md. Any trivial items offer to
   address as a separate commit.
7. Do not push. After the work is green, present the work summary and ASK whether to commit
   and push to dev (this triggers CI; PR-to-main is required for promotion).

Re-pinned tests: DTI assertions for raise-applicable fixtures become the engine-derived
value with MED-06 / F-032 citations and the arithmetic.

Work summary format (use these labels verbatim):
A. Verification: claims you re-verified; any drift found.
B. Files changed: list with one-line purpose each.
C. Tests added/modified: ID, name, hand-computed Decimal + arithmetic comment.
D. Re-pinned tests: finding ID + arithmetic per assertion, or "none".
E. Targeted-suite and full-suite final summary lines, verbatim from pytest.
F. Pylint final line, verbatim.
G. Migrations: upgrade->downgrade->upgrade result, or "n/a".
H. Invariants: which existing regression locks stayed green; which new locks added.
I. Discovered refinements folded in: each with rationale.
J. OUT OF SCOPE -- flagged, not fixed: file:line + reason per item + F-# entry in remediation_follow_up.md.
K. Open questions / assumptions made.
L. Proposed commit message (format: <type>(<scope>): <what> with the Co-Authored-By trailer
   per CLAUDE.md).
M. Ask: "Ready to commit and push to dev?"

Specific verification gates for this commit:
- Salary with a recurring 3% raise inside the projection window: DTI denominator and
  ratio match the paycheck engine output, hand-computed (was off by ~1 percentage point
  in the worked example).
- Salary with no raise: DTI identical to before (no regression).
- `grep -n "Decimal(\"26\") / Decimal(\"12\")" app/services/savings_dashboard_service.py`
  shows no instance in the DTI path.

If anything is unclear, ASK.
```

---

### Commit 27 -- `fix(interest): leap-year day count + biweekly residue reconcile (MED-05)`

**Prereqs on dev:** 1 (round_money). **Closes:** MED-05 / PA-06 / PA-07 (leap-year overstatement;
biweekly residue).

```text
You are executing Commit 27 of the Shekel financial-calculation audit remediation in a
fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/audits/financial_calculations/remediation_plan.md (Sections 0-7; Section 9 "Commit
  27" A-H)
- @CLAUDE.md, @docs/coding-standards.md, @docs/testing-standards.md
- @docs/audits/financial_calculations/08_findings.md (MED-05)
- @app/services/interest_projection.py (DAYS_IN_YEAR hardcoded 365; the module docstring
  already explains the trade-off -- this commit fixes it properly)
- @app/services/paycheck_calculator.py (the gross_biweekly quantize residue -- the module
  docstring already explains the residue example)
- @app/utils/money.py (Commit 1)

Objective: (1) interest_projection threads the actual day count for the projection window
(366 in leap years) instead of a hardcoded 365 -- full-precision Decimal math, round_money
only at the boundary. (2) paycheck_calculator reconciles the per-cycle biweekly
quantization residue into the annual aggregate so 26 periods sum to the exact annual
figure (deterministic distribution; reproducible across runs).

Production files this commit touches:
- app/services/interest_projection.py
- app/services/paycheck_calculator.py
- tests/test_services/test_interest_projection.py
- tests/test_services/test_paycheck_calculator.py

Apply these rules (the plan's Section 1 is the authoritative version):
1. The plan's specification for this commit (Section 9, subsections A through H) is the
   floor, not the ceiling. If verification surfaces extra in-scope refinements, fold them
   in and explain in the work summary.
2. Trust-but-verify: re-grep every cited symbol; read every file you will change in full;
   confirm the audit/plan claim still holds against current code before editing. If reality
   has drifted, stop and report in the work summary before continuing.
3. No shortcuts, no band-aid fixes. Fix root causes. Decimal money from strings, IDs and
   semantic booleans for business logic (never name strings), DRY/SOLID, fully normalized
   schema, pythonic type-hinted code, specific exceptions, substantive docstrings, no
   Unicode em/en dashes (use -- or -).
4. Never modify a test to make it pass except for the documented exception in plan Section 1
   rule 2: tests pinning a shipping wrong number this finding proves wrong. Every re-pinned
   assertion gets a comment naming the finding ID and the hand-computed arithmetic. List
   each in the work summary.
5. Targeted pytest during edits; pylint app/ --fail-on=E,F clean (no new warnings vs
   baseline); full pytest (-n 12 default) green as the per-commit final gate. Migrations
   (if any) round-trip upgrade->downgrade->upgrade cleanly; destructive changes get a Review
   docstring line and explicit developer approval before authoring.
6. Stay in scope. Out-of-scope issues, gold-plating opportunities, or refactors you noticed but did not
   perform MUST be flagged in the work summary with file:line and a one-sentence reason for not acting.
   Do not silently fold them in. Items that are not directly handled by a future commit need to be
   added to docs/audits/financial_calculations/remediation_follow_up.md. Any trivial items offer to
   address as a separate commit.
7. Do not push. After the work is green, present the work summary and ASK whether to commit
   and push to dev (this triggers CI; PR-to-main is required for promotion).

Re-pinned tests: leap-year interest assertions and annual-gross reconciliation assertions
become the hand-computed corrected values with MED-05 / PA-06 / PA-07 citations.

Work summary format (use these labels verbatim):
A. Verification: claims you re-verified; any drift found.
B. Files changed: list with one-line purpose each.
C. Tests added/modified: ID, name, hand-computed Decimal + arithmetic comment.
D. Re-pinned tests: finding ID + arithmetic per assertion, or "none".
E. Targeted-suite and full-suite final summary lines, verbatim from pytest.
F. Pylint final line, verbatim.
G. Migrations: upgrade->downgrade->upgrade result, or "n/a".
H. Invariants: which existing regression locks stayed green; which new locks added.
I. Discovered refinements folded in: each with rationale.
J. OUT OF SCOPE -- flagged, not fixed: file:line + reason per item + F-# entry in remediation_follow_up.md.
K. Open questions / assumptions made.
L. Proposed commit message (format: <type>(<scope>): <what> with the Co-Authored-By trailer
   per CLAUDE.md).
M. Ask: "Ready to commit and push to dev?"

Specific verification gates for this commit:
- A projection window crossing Feb 29 uses 366 in the day-count divisor (hand-computed
  delta from the pre-fix 365-based value).
- A non-leap-year projection is unchanged (no regression).
- Sum of 26 biweekly gross values == annual salary exactly (was off by the residue).
- Residue distribution is deterministic across repeat runs (assert by computing twice).

If anything is unclear, ASK.
```

## Group F -- MEDIUM structural (28-32)

### Commit 28 -- `refactor(investment): extract dashboard service; collapse dispatcher; DTO (MED-01)`

**Prereqs on dev:** 13 (loan_resolver provides the shape for the LoanInputs DTO). **Closes:** MED-01
/ S6-01 / S6-03 / S6-04 / S6-06 / S6-07.

```text
You are executing Commit 28 of the Shekel financial-calculation audit remediation in a
fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/audits/financial_calculations/remediation_plan.md (Sections 0-7; Section 9 "Commit
  28" A-H)
- @CLAUDE.md (Architecture and Boundaries; services importing flask is forbidden)
- @docs/coding-standards.md, @docs/testing-standards.md
- @docs/audits/financial_calculations/06_dry_solid.md (S6-01 through S6-07 with the live
  metrics and exact anchor lines)
- @app/routes/investment.py (the route bodies mixing HTTP + ORM + logic; ~295/241 lines)
- @app/routes/savings.py (the model to mirror: it is already a thin delegator)
- @app/services/savings_dashboard_service.py (the existing dispatcher and where the
  hardcoded _DEDUCTION_PATH_TYPES sits)
- @app/services/year_end_summary_service.py (loan path dispatcher; 11-key ctx + 4-key
  base_args ISP issue)
- @app/services/amortization_engine.py (get_loan_projection duck-types params)
- @app/services/loan_resolver.py (Commit 13 -- defines the LoanInputs shape the engine
  will consume via DTO)

Objective: pure structural refactor; outputs MUST NOT change (assert-unchanged across
fixtures is the load-bearing gate). (a) extract app/services/investment_dashboard_service
.py containing the dashboard + growth-chart computation (no flask); reduce investment.py
to a thin delegator mirroring savings.py. (b) collapse the two per-account-type
dispatchers into one flag-driven dispatcher (has_amortization, has_interest, is_escrow,
is_401k) replacing _DEDUCTION_PATH_TYPES and the savings/year-end split. (c) declare a
frozen LoanInputs dataclass DTO that get_loan_projection accepts instead of duck-typing.
(d) ISP narrowing: helpers in year_end_summary_service take only the fields they read,
not the whole ctx/base_args bag (keep the W-052 sanctioned load-once pattern; only the
parameter surface narrows).

Production files this commit touches (re-grep all line numbers):
- app/services/investment_dashboard_service.py (new)
- app/services/account_projection.py (new; or consolidate into resolver layer if cleaner)
- app/routes/investment.py (thin delegator)
- app/services/savings_dashboard_service.py (use flag-driven dispatcher)
- app/services/year_end_summary_service.py (use flag-driven dispatcher; ISP narrowing)
- app/services/amortization_engine.py (LoanInputs DTO accepted by get_loan_projection)
- relevant tests assert-unchanged across fixtures

Apply these rules (the plan's Section 1 is the authoritative version):
1. The plan's specification for this commit (Section 9, subsections A through H) is the
   floor, not the ceiling. If verification surfaces extra in-scope refinements, fold them
   in and explain in the work summary.
2. Trust-but-verify: re-grep every cited symbol; read every file you will change in full;
   confirm the audit/plan claim still holds against current code before editing. If reality
   has drifted, stop and report in the work summary before continuing.
3. No shortcuts, no band-aid fixes. Fix root causes. Decimal money from strings, IDs and
   semantic booleans for business logic (never name strings), DRY/SOLID, fully normalized
   schema, pythonic type-hinted code, specific exceptions, substantive docstrings, no
   Unicode em/en dashes (use -- or -).
4. Never modify a test to make it pass except for the documented exception in plan Section 1
   rule 2: tests pinning a shipping wrong number this finding proves wrong. Every re-pinned
   assertion gets a comment naming the finding ID and the hand-computed arithmetic. List
   each in the work summary.
5. Targeted pytest during edits; pylint app/ --fail-on=E,F clean (no new warnings vs
   baseline); full pytest (-n 12 default) green as the per-commit final gate. Migrations
   (if any) round-trip upgrade->downgrade->upgrade cleanly; destructive changes get a Review
   docstring line and explicit developer approval before authoring.
6. Stay in scope. Out-of-scope issues, gold-plating opportunities, or refactors you noticed but did not
   perform MUST be flagged in the work summary with file:line and a one-sentence reason for not acting.
   Do not silently fold them in. Items that are not directly handled by a future commit need to be
   added to docs/audits/financial_calculations/remediation_follow_up.md. Any trivial items offer to
   address as a separate commit.
7. Do not push. After the work is green, present the work summary and ASK whether to commit
   and push to dev (this triggers CI; PR-to-main is required for promotion).

Work summary format (use these labels verbatim):
A. Verification: claims you re-verified; any drift found.
B. Files changed: list with one-line purpose each.
C. Tests added/modified: ID, name, hand-computed Decimal + arithmetic comment.
D. Re-pinned tests: finding ID + arithmetic per assertion, or "none".
E. Targeted-suite and full-suite final summary lines, verbatim from pytest.
F. Pylint final line, verbatim.
G. Migrations: upgrade->downgrade->upgrade result, or "n/a".
H. Invariants: which existing regression locks stayed green; which new locks added.
I. Discovered refinements folded in: each with rationale.
J. OUT OF SCOPE -- flagged, not fixed: file:line + reason per item + F-# entry in remediation_follow_up.md.
K. Open questions / assumptions made.
L. Proposed commit message (format: <type>(<scope>): <what> with the Co-Authored-By trailer
   per CLAUDE.md).
M. Ask: "Ready to commit and push to dev?"

Specific verification gates for this commit:
- /investment dashboard and growth chart output: byte-identical to before this commit
  on every fixture (the structural refactor must not change a number).
- `grep -nE "^(from|import)\s+flask" app/services/investment_dashboard_service.py` empty.
- Single dispatcher: `grep -rn "_DEDUCTION_PATH_TYPES\|frozenset\(\[AcctTypeEnum\." app/`
  empty after this commit.
- LoanInputs DTO is a declared dataclass; get_loan_projection signature accepts it.
- ISP check: at least one helper that previously read 1-2 keys from a whole ctx now
  takes those fields explicitly (audit-cited helpers in year_end_summary_service).

If anything is unclear, ASK. Do NOT change calculation math; this is structure only. If
you find a calculation defect while refactoring, STOP and report -- do not fix it here
(scope rule).
```

---

### Commit 29 -- `refactor(status): route residual inline/Jinja predicates through helper (MED-02)`

**Prereqs on dev:** 2 (predicate exists), 5 and 10 (the main balance/subtotal sites already routed).
**Closes:** MED-02 residual / D6-09 (E-15 fully realized).

```text
You are executing Commit 29 of the Shekel financial-calculation audit remediation in a
fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/audits/financial_calculations/remediation_plan.md (Sections 0-7; Section 9 "Commit
  29" A-H)
- @CLAUDE.md (IDs for logic, strings for display only)
- @docs/coding-standards.md, @docs/testing-standards.md
- @docs/audits/financial_calculations/06_dry_solid.md (D6-09 with the 20+ inline status
  sites in three forms: inline Python skip, SQLAlchemy filter, Jinja conditional)
- @app/utils/balance_predicates.py (Commit 2; the helper you route everything through)
- All app/services/* and app/routes/* still containing inline status comparisons (re-grep
  to enumerate; the audit's lines have drifted)
- @app/templates/grid/* and any other templates referencing status names or status IDs

Objective: behavior-preserving consolidation. After this commit, business-logic status
comparisons live in one place. Inline `status_id == projected_id` filters, SQLAlchemy
status filters, and Jinja `status.name ==` conditionals are routed through the predicate
or via context-provided booleans/IDs. Templates compare IDs only (CLAUDE.md: IDs not
names in conditionals).

Production files this commit touches (re-grep to enumerate):
- The remaining services with inline status checks (likely balance_calculator,
  carry_forward_service, transaction_service helpers, dashboard_service helpers)
- The relevant routes
- The relevant templates
- Tests: assert-unchanged across fixtures

Apply these rules (the plan's Section 1 is the authoritative version):
1. The plan's specification for this commit (Section 9, subsections A through H) is the
   floor, not the ceiling. If verification surfaces extra in-scope refinements, fold them
   in and explain in the work summary.
2. Trust-but-verify: re-grep every cited symbol; read every file you will change in full;
   confirm the audit/plan claim still holds against current code before editing. If reality
   has drifted, stop and report in the work summary before continuing.
3. No shortcuts, no band-aid fixes. Fix root causes. Decimal money from strings, IDs and
   semantic booleans for business logic (never name strings), DRY/SOLID, fully normalized
   schema, pythonic type-hinted code, specific exceptions, substantive docstrings, no
   Unicode em/en dashes (use -- or -).
4. Never modify a test to make it pass except for the documented exception in plan Section 1
   rule 2: tests pinning a shipping wrong number this finding proves wrong. Every re-pinned
   assertion gets a comment naming the finding ID and the hand-computed arithmetic. List
   each in the work summary.
5. Targeted pytest during edits; pylint app/ --fail-on=E,F clean (no new warnings vs
   baseline); full pytest (-n 12 default) green as the per-commit final gate. Migrations
   (if any) round-trip upgrade->downgrade->upgrade cleanly; destructive changes get a Review
   docstring line and explicit developer approval before authoring.
6. Stay in scope. Out-of-scope issues, gold-plating opportunities, or refactors you noticed but did not
   perform MUST be flagged in the work summary with file:line and a one-sentence reason for not acting.
   Do not silently fold them in. Items that are not directly handled by a future commit need to be
   added to docs/audits/financial_calculations/remediation_follow_up.md. Any trivial items offer to
   address as a separate commit.
7. Do not push. After the work is green, present the work summary and ASK whether to commit
   and push to dev (this triggers CI; PR-to-main is required for promotion).

Work summary format (use these labels verbatim):
A. Verification: claims you re-verified; any drift found.
B. Files changed: list with one-line purpose each.
C. Tests added/modified: ID, name, hand-computed Decimal + arithmetic comment.
D. Re-pinned tests: finding ID + arithmetic per assertion, or "none".
E. Targeted-suite and full-suite final summary lines, verbatim from pytest.
F. Pylint final line, verbatim.
G. Migrations: upgrade->downgrade->upgrade result, or "n/a".
H. Invariants: which existing regression locks stayed green; which new locks added.
I. Discovered refinements folded in: each with rationale.
J. OUT OF SCOPE -- flagged, not fixed: file:line + reason per item + F-# entry in remediation_follow_up.md.
K. Open questions / assumptions made.
L. Proposed commit message (format: <type>(<scope>): <what> with the Co-Authored-By trailer
   per CLAUDE.md).
M. Ask: "Ready to commit and push to dev?"

Specific verification gates for this commit:
- `grep -rn "status_id != \|status_id == \|status\.name ==" app/ | grep -v
  balance_predicates.py | grep -v ref_cache.py` shows ONLY ref-data setup / display-label
  uses, no business logic.
- `[CREDIT, CANCELLED]` ID-set re-derivations: removed; the helper is the only definition.
- Templates use `txn.status_id == PROJECTED_ID` or boolean flags from the context, never
  `status.name`.
- Grid/dashboard/savings pinned values remain byte-identical (no number changed).

If anything is unclear, ASK. If you find a status comparison whose behavior cannot be
reduced to the predicate (it tests a more specific status), document it and either extend
the predicate module (DRY) or leave it with a comment explaining why it is genuinely
different. Do not silently leave a bypass.
```

---

### Commit 30 -- `fix(dashboard): entry-tracked bill row single disclosed base (E-21, MED-03)`

**Prereqs on dev:** 5 (the producer). **Closes:** MED-03 / F-028 / F-056 (E-21 -- single declared
base for entry-tracked rows).

```text
You are executing Commit 30 of the Shekel financial-calculation audit remediation in a
fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/audits/financial_calculations/remediation_plan.md (Sections 0-7; Section 9 "Commit
  30" A-H)
- @CLAUDE.md, @docs/coding-standards.md, @docs/testing-standards.md
- @docs/audits/financial_calculations/08_findings.md (MED-03)
- @docs/audits/financial_calculations/03_consistency.md (F-028, F-056 -- the row's amount
  uses effective_amount while remaining/over-budget uses estimated, two undisclosed bases)
- @app/services/dashboard_service.py (the bill-row render, _entry_progress_fields)
- @app/services/entry_service.py (compute_remaining)
- @app/templates/* (the bill row template; surface the base label per E-21)
- @app/services/balance_resolver.py (Commit 5; uses the same predicates/rounding)

Objective: per E-21, the entry-tracked bill row's amount, remaining, and over-budget are
computed against ONE declared base (estimated_amount per E-21) and the base is disclosed
in the UI. Internal consistency: amount cell and over-budget flag agree about the same
base. The over-budget True/False is a derived function of the same numbers, not a parallel
computation.

Production files this commit touches:
- app/services/dashboard_service.py (the row render)
- app/services/entry_service.py (compute_remaining; the base is now an explicit parameter
  or a documented constant, not implicit)
- app/templates/<the bill-row template> (label the base)
- tests/test_services/test_dashboard_service.py
- tests/test_services/test_entry_service.py

Apply these rules (the plan's Section 1 is the authoritative version):
1. The plan's specification for this commit (Section 9, subsections A through H) is the
   floor, not the ceiling. If verification surfaces extra in-scope refinements, fold them
   in and explain in the work summary.
2. Trust-but-verify: re-grep every cited symbol; read every file you will change in full;
   confirm the audit/plan claim still holds against current code before editing. If reality
   has drifted, stop and report in the work summary before continuing.
3. No shortcuts, no band-aid fixes. Fix root causes. Decimal money from strings, IDs and
   semantic booleans for business logic (never name strings), DRY/SOLID, fully normalized
   schema, pythonic type-hinted code, specific exceptions, substantive docstrings, no
   Unicode em/en dashes (use -- or -).
4. Never modify a test to make it pass except for the documented exception in plan Section 1
   rule 2: tests pinning a shipping wrong number this finding proves wrong. Every re-pinned
   assertion gets a comment naming the finding ID and the hand-computed arithmetic. List
   each in the work summary.
5. Targeted pytest during edits; pylint app/ --fail-on=E,F clean (no new warnings vs
   baseline); full pytest (-n 12 default) green as the per-commit final gate. Migrations
   (if any) round-trip upgrade->downgrade->upgrade cleanly; destructive changes get a Review
   docstring line and explicit developer approval before authoring.
6. Stay in scope. Out-of-scope issues, gold-plating opportunities, or refactors you noticed but did not
   perform MUST be flagged in the work summary with file:line and a one-sentence reason for not acting.
   Do not silently fold them in. Items that are not directly handled by a future commit need to be
   added to docs/audits/financial_calculations/remediation_follow_up.md. Any trivial items offer to
   address as a separate commit.
7. Do not push. After the work is green, present the work summary and ASK whether to commit
   and push to dev (this triggers CI; PR-to-main is required for promotion).

Re-pinned tests: row remaining/over-budget assertions that pinned the mixed-base value
become the single-base value with MED-03 / F-028 / F-056 citations and the arithmetic.

Work summary format (use these labels verbatim):
A. Verification: claims you re-verified; any drift found.
B. Files changed: list with one-line purpose each.
C. Tests added/modified: ID, name, hand-computed Decimal + arithmetic comment.
D. Re-pinned tests: finding ID + arithmetic per assertion, or "none".
E. Targeted-suite and full-suite final summary lines, verbatim from pytest.
F. Pylint final line, verbatim.
G. Migrations: upgrade->downgrade->upgrade result, or "n/a".
H. Invariants: which existing regression locks stayed green; which new locks added.
I. Discovered refinements folded in: each with rationale.
J. OUT OF SCOPE -- flagged, not fixed: file:line + reason per item + F-# entry in remediation_follow_up.md.
K. Open questions / assumptions made.
L. Proposed commit message (format: <type>(<scope>): <what> with the Co-Authored-By trailer
   per CLAUDE.md).
M. Ask: "Ready to commit and push to dev?"

Specific verification gates for this commit:
- Hand-computed Decimal for the row (actual=100, est=120, entries summing 80): amount and
  remaining and over-budget are all consistent on the one base (E-21 = estimated_amount).
- Over-budget flag agrees with the displayed amount/remaining for a clearly-overspent
  envelope and for an under-budget envelope.
- The UI labels the base (manual verification step: render the row in dark mode and at
  the mobile breakpoint, confirm the base is visible).

If anything is unclear, ASK. E-21's declared base is estimated_amount unconditionally; if
the developer's intent shifts during your work, STOP and ask -- do not silently change
the base.
```

---

### Commit 31 -- `refactor(templates): move money math out of Jinja/JS into services (MED-04)`

**Prereqs on dev:** the producers from earlier commits exist (5, 13, 23).
**Closes:** MED-04 / E-16 / E-17 (Jinja and JS arithmetic on money; |float casts).

```text
You are executing Commit 31 of the Shekel financial-calculation audit remediation in a
fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/audits/financial_calculations/remediation_plan.md (Sections 0-7; Section 9 "Commit
  31" A-H)
- @CLAUDE.md (Templates section -- no money math in templates)
- @docs/coding-standards.md (HTML/Jinja Logic Boundaries; JavaScript "monetary values
  display-only")
- @docs/audits/financial_calculations/01_inventory.md (TA-01..TA-11 the 11 Jinja
  arithmetic sites; JN-01..JN-03 the JS sites)
- the audit-cited templates (re-grep): app/templates/grid/_transaction_cell.html (estimated
  - entries.total), app/templates/loan/_escrow_list.html (annual|float / 12 -- the float
  cast on Decimal), and the other 9 sites
- app/static/js/retirement_gap_chart.js, app/static/js/chart_variance.js (the 3 JS
  recompute sites)
- the routes/services that own the values being rendered

Objective: every money computation moves out of Jinja and JS into the owning route or
service, in Decimal. Templates only render; JS only displays. Eliminate every |float
cast on a Decimal value. Assert that displayed values are byte-identical to before this
commit (Phase 3 said they agreed numerically today; the structural fix protects against
silent drift if a server formula changes later).

Production files this commit touches:
- the 11 Jinja templates listed in 01_inventory.md TA register
- the 3 JS files listed (JN register)
- the routes/services that compute and pass the values
- targeted tests assert-unchanged

Apply these rules (the plan's Section 1 is the authoritative version):
1. The plan's specification for this commit (Section 9, subsections A through H) is the
   floor, not the ceiling. If verification surfaces extra in-scope refinements, fold them
   in and explain in the work summary.
2. Trust-but-verify: re-grep every cited symbol; read every file you will change in full;
   confirm the audit/plan claim still holds against current code before editing. If reality
   has drifted, stop and report in the work summary before continuing.
3. No shortcuts, no band-aid fixes. Fix root causes. Decimal money from strings, IDs and
   semantic booleans for business logic (never name strings), DRY/SOLID, fully normalized
   schema, pythonic type-hinted code, specific exceptions, substantive docstrings, no
   Unicode em/en dashes (use -- or -).
4. Never modify a test to make it pass except for the documented exception in plan Section 1
   rule 2: tests pinning a shipping wrong number this finding proves wrong. Every re-pinned
   assertion gets a comment naming the finding ID and the hand-computed arithmetic. List
   each in the work summary.
5. Targeted pytest during edits; pylint app/ --fail-on=E,F clean (no new warnings vs
   baseline); full pytest (-n 12 default) green as the per-commit final gate. Migrations
   (if any) round-trip upgrade->downgrade->upgrade cleanly; destructive changes get a Review
   docstring line and explicit developer approval before authoring.
6. Stay in scope. Out-of-scope issues, gold-plating opportunities, or refactors you noticed but did not
   perform MUST be flagged in the work summary with file:line and a one-sentence reason for not acting.
   Do not silently fold them in. Items that are not directly handled by a future commit need to be
   added to docs/audits/financial_calculations/remediation_follow_up.md. Any trivial items offer to
   address as a separate commit.
7. Do not push. After the work is green, present the work summary and ASK whether to commit
   and push to dev (this triggers CI; PR-to-main is required for promotion).

Work summary format (use these labels verbatim):
A. Verification: claims you re-verified; any drift found.
B. Files changed: list with one-line purpose each.
C. Tests added/modified: ID, name, hand-computed Decimal + arithmetic comment.
D. Re-pinned tests: finding ID + arithmetic per assertion, or "none".
E. Targeted-suite and full-suite final summary lines, verbatim from pytest.
F. Pylint final line, verbatim.
G. Migrations: upgrade->downgrade->upgrade result, or "n/a".
H. Invariants: which existing regression locks stayed green; which new locks added.
I. Discovered refinements folded in: each with rationale.
J. OUT OF SCOPE -- flagged, not fixed: file:line + reason per item + F-# entry in remediation_follow_up.md.
K. Open questions / assumptions made.
L. Proposed commit message (format: <type>(<scope>): <what> with the Co-Authored-By trailer
   per CLAUDE.md).
M. Ask: "Ready to commit and push to dev?"

Specific verification gates for this commit:
- `grep -nE "\{\{[^}]*[-+*/][^}]*\}\}" app/templates/` shows no arithmetic on money
  variables (allow non-money like loop counters).
- `grep -nE "\|float" app/templates/` is empty (the |float cast on Decimal is eliminated).
- `grep -rnE "(act|est|amount|balance)\s*[-+*/]\s*(act|est|amount|balance)" app/static/
  js/` is empty (no monetary arithmetic in JS).
- Render checks (manual): grid cell remaining, escrow per-period, retirement-gap chart,
  variance tooltip render byte-identical to before this commit (paste before/after).

If anything is unclear, ASK. The retirement-gap chart and variance tooltip JS already
have data they should be rendering; you are moving the math, not the data shape -- so
the data the route emits must contain the computed values, not the raw inputs.
```

---

### Commit 32 -- `test(calc): replace loose assertions; add invariant coverage (MED-07)`

**Prereqs on dev:** the producers being tested exist (broadly, the relevant earlier commits).
**Closes:** MED-07 / PA-12..PA-30 residue (directional / is-not-None tests; missing invariant
coverage).

```text
You are executing Commit 32 of the Shekel financial-calculation audit remediation in a
fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/audits/financial_calculations/remediation_plan.md (Sections 0-7; Section 9 "Commit
  32" A-H)
- @CLAUDE.md (rule 4: never ignore a problem; if a pinned value reveals a real bug, stop
  and report -- do not paper over)
- @docs/coding-standards.md, @docs/testing-standards.md (testing-standards is the
  authoritative source for assertion quality; read it in full)
- @docs/audits/financial_calculations/07_test_gaps.md (the LOOSE-ONLY verdicts and the
  missing-invariant inventory)
- The targeted test files: debt-balance depth, sad paths, HYSA full-year compounding,
  paycheck/tax negative paths + annual reconciliation, transfer-recurrence boundaries,
  amortization extra-payment, growth-engine

Objective: replace directional / is-not-None / loose tests with exact hand-computed
Decimal expectations; add the missing invariant tests (relationships like net == gross -
tax - deductions, annual tax reconciliation, status-machine legal/illegal transitions,
boundary/sad-path). This is a TEST-ONLY commit. If pinning a value surfaces a real
defect, STOP and report; that becomes a separate root-cause fix (and would be in scope
for the existing find's commit, not silently folded in here).

Production files this commit touches:
- tests/test_services/*, tests/test_routes/* (the LOOSE files identified in 07_test_gaps
  Part 7.A)

Apply these rules (the plan's Section 1 is the authoritative version):
1. The plan's specification for this commit (Section 9, subsections A through H) is the
   floor, not the ceiling. If verification surfaces extra in-scope refinements, fold them
   in and explain in the work summary.
2. Trust-but-verify: re-grep every cited symbol; read every file you will change in full;
   confirm the audit/plan claim still holds against current code before editing. If reality
   has drifted, stop and report in the work summary before continuing.
3. No shortcuts, no band-aid fixes. Fix root causes. Decimal money from strings, IDs and
   semantic booleans for business logic (never name strings), DRY/SOLID, fully normalized
   schema, pythonic type-hinted code, specific exceptions, substantive docstrings, no
   Unicode em/en dashes (use -- or -).
4. Never modify a test to make it pass except for the documented exception in plan Section 1
   rule 2: tests pinning a shipping wrong number this finding proves wrong. Every re-pinned
   assertion gets a comment naming the finding ID and the hand-computed arithmetic. List
   each in the work summary.
5. Targeted pytest during edits; pylint app/ --fail-on=E,F clean (no new warnings vs
   baseline); full pytest (-n 12 default) green as the per-commit final gate. Migrations
   (if any) round-trip upgrade->downgrade->upgrade cleanly; destructive changes get a Review
   docstring line and explicit developer approval before authoring.
6. Stay in scope. Out-of-scope issues, gold-plating opportunities, or refactors you noticed but did not
   perform MUST be flagged in the work summary with file:line and a one-sentence reason for not acting.
   Do not silently fold them in. Items that are not directly handled by a future commit need to be
   added to docs/audits/financial_calculations/remediation_follow_up.md. Any trivial items offer to
   address as a separate commit.
7. Do not push. After the work is green, present the work summary and ASK whether to commit
   and push to dev (this triggers CI; PR-to-main is required for promotion).

Note for this commit (rule 4 nuance): the re-pin rule does NOT apply here -- this is not
re-pinning an audit-confirmed-wrong value; this is converting a loose assertion to a
pinned one. Every new pin still carries the hand-computed arithmetic in a comment. If a
newly-pinned value surfaces a real defect, STOP and report (CLAUDE.md rule 4); do not
silently fix in this commit.

Work summary format (use these labels verbatim):
A. Verification: claims you re-verified; any drift found.
B. Files changed: list with one-line purpose each.
C. Tests added/modified: ID, name, hand-computed Decimal + arithmetic comment.
D. Re-pinned tests: finding ID + arithmetic per assertion, or "none".
E. Targeted-suite and full-suite final summary lines, verbatim from pytest.
F. Pylint final line, verbatim.
G. Migrations: upgrade->downgrade->upgrade result, or "n/a".
H. Invariants: which existing regression locks stayed green; which new locks added.
I. Discovered refinements folded in: each with rationale.
J. OUT OF SCOPE -- flagged, not fixed: file:line + reason per item + F-# entry in remediation_follow_up.md.
K. Open questions / assumptions made.
L. Proposed commit message (format: <type>(<scope>): <what> with the Co-Authored-By trailer
   per CLAUDE.md).
M. Ask: "Ready to commit and push to dev?"

Specific verification gates for this commit:
- `grep -rn "is not None\|is None\|> 0\|>= 0" tests/test_services/ tests/test_routes/`
  count drops by a documented amount (cite the affected files and the count delta).
- New invariant tests added: enumerate them in the work summary with the relationship
  they assert.
- If any newly-pinned value surfaced a defect, the work summary section A names it and
  K asks how to proceed; do NOT silently fix in this commit.

If anything is unclear, ASK.
```

---

## Group G -- LOW + cleanup + final gate (33-37)

### Commit 33 -- `chore(tax): delete dead legacy calculate_federal_tax + its test (LOW-01)`

**Prereqs on dev:** none. **Closes:** LOW-01 / F-040 (dead code carrying an inert divergence).

```text
You are executing Commit 33 of the Shekel financial-calculation audit remediation in a
fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/audits/financial_calculations/remediation_plan.md (Sections 0-7; Section 9 "Commit
  33" A-H)
- @CLAUDE.md (rule 7: trace impact before changing interfaces; rule 5 nuance: deleting a
  test for deleted code is NOT modifying a test to pass)
- @docs/coding-standards.md, @docs/testing-standards.md
- @app/services/tax_calculator.py (calculate_federal_tax, ~`:215-234`)
- @tests/test_services/test_tax_calculator.py (TestLegacyWrapper, ~`:510-527`)

Objective: re-confirm zero app/ callers via grep; delete the function and its dedicated
test class together in one atomic commit so neither dangles. Live engine assertions are
unchanged (assert-unchanged).

Production files this commit touches:
- app/services/tax_calculator.py
- tests/test_services/test_tax_calculator.py

Apply these rules (the plan's Section 1 is the authoritative version):
1. The plan's specification for this commit (Section 9, subsections A through H) is the
   floor, not the ceiling. If verification surfaces extra in-scope refinements, fold them
   in and explain in the work summary.
2. Trust-but-verify: re-grep every cited symbol; read every file you will change in full;
   confirm the audit/plan claim still holds against current code before editing. If reality
   has drifted, stop and report in the work summary before continuing.
3. No shortcuts, no band-aid fixes. Fix root causes. Decimal money from strings, IDs and
   semantic booleans for business logic (never name strings), DRY/SOLID, fully normalized
   schema, pythonic type-hinted code, specific exceptions, substantive docstrings, no
   Unicode em/en dashes (use -- or -).
4. Never modify a test to make it pass except for the documented exception in plan Section 1
   rule 2: tests pinning a shipping wrong number this finding proves wrong. Every re-pinned
   assertion gets a comment naming the finding ID and the hand-computed arithmetic. List
   each in the work summary.
5. Targeted pytest during edits; pylint app/ --fail-on=E,F clean (no new warnings vs
   baseline); full pytest (-n 12 default) green as the per-commit final gate. Migrations
   (if any) round-trip upgrade->downgrade->upgrade cleanly; destructive changes get a Review
   docstring line and explicit developer approval before authoring.
6. Stay in scope. Out-of-scope issues, gold-plating opportunities, or refactors you noticed but did not
   perform MUST be flagged in the work summary with file:line and a one-sentence reason for not acting.
   Do not silently fold them in. Items that are not directly handled by a future commit need to be
   added to docs/audits/financial_calculations/remediation_follow_up.md. Any trivial items offer to
   address as a separate commit.
7. Do not push. After the work is green, present the work summary and ASK whether to commit
   and push to dev (this triggers CI; PR-to-main is required for promotion).

Work summary format (use these labels verbatim):
A. Verification: claims you re-verified; any drift found.
B. Files changed: list with one-line purpose each.
C. Tests added/modified: ID, name, hand-computed Decimal + arithmetic comment.
D. Re-pinned tests: finding ID + arithmetic per assertion, or "none".
E. Targeted-suite and full-suite final summary lines, verbatim from pytest.
F. Pylint final line, verbatim.
G. Migrations: upgrade->downgrade->upgrade result, or "n/a".
H. Invariants: which existing regression locks stayed green; which new locks added.
I. Discovered refinements folded in: each with rationale.
J. OUT OF SCOPE -- flagged, not fixed: file:line + reason per item + F-# entry in remediation_follow_up.md.
K. Open questions / assumptions made.
L. Proposed commit message (format: <type>(<scope>): <what> with the Co-Authored-By trailer
   per CLAUDE.md).
M. Ask: "Ready to commit and push to dev?"

Specific verification gates for this commit:
- `grep -rn "calculate_federal_tax" app/` returns empty after the delete.
- `pytest tests/test_services/test_tax_calculator.py -v` green (the surviving live engine
  tests run cleanly; TestLegacyWrapper is gone).
- Live engine assertions byte-identical to before this commit.

If anything is unclear, ASK. If you find a caller after all (any caller outside the test
class itself), STOP and report -- the function is not dead and the commit is invalid.
```

---

### Commit 34 -- `fix(transfer): route recurrence regen delete through transfer_service (LOW-02)`

**Prereqs on dev:** none. **Closes:** LOW-02 / B6-03 (Transfer Invariant 4 forensic nuance; FK
cascade already kept the pair atomic).

```text
You are executing Commit 34 of the Shekel financial-calculation audit remediation in a
fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/audits/financial_calculations/remediation_plan.md (Sections 0-7; Section 9 "Commit
  34" A-H)
- @CLAUDE.md (Transfer Invariants, especially Invariant 4)
- @docs/coding-standards.md, @docs/testing-standards.md
- @docs/audits/financial_calculations/06_dry_solid.md (B6-03 with the exact line of the
  bare delete and the canonical service path)
- @app/services/transfer_recurrence.py (~`:200-201`, the bare db.session.delete)
- @app/services/transfer_service.py (delete_transfer with orphan verify and
  EVT_TRANSFER_HARD_DELETED audit event)
- @app/utils/log_events.py (the event constants)

Objective: regenerate_for_template's deletion loop calls transfer_service.delete_transfer
instead of bare db.session.delete. This restores the orphan-verification self-check and
the EVT_TRANSFER_HARD_DELETED audit event for regen-driven deletes. Shadow-pair atomicity
was already protected by FK cascade, so there is no balance change; the gain is forensic
completeness and Transfer Invariant 4 holding literally.

Production files this commit touches:
- app/services/transfer_recurrence.py
- tests/test_services/test_transfer_recurrence.py

Apply these rules (the plan's Section 1 is the authoritative version):
1. The plan's specification for this commit (Section 9, subsections A through H) is the
   floor, not the ceiling. If verification surfaces extra in-scope refinements, fold them
   in and explain in the work summary.
2. Trust-but-verify: re-grep every cited symbol; read every file you will change in full;
   confirm the audit/plan claim still holds against current code before editing. If reality
   has drifted, stop and report in the work summary before continuing.
3. No shortcuts, no band-aid fixes. Fix root causes. Decimal money from strings, IDs and
   semantic booleans for business logic (never name strings), DRY/SOLID, fully normalized
   schema, pythonic type-hinted code, specific exceptions, substantive docstrings, no
   Unicode em/en dashes (use -- or -).
4. Never modify a test to make it pass except for the documented exception in plan Section 1
   rule 2: tests pinning a shipping wrong number this finding proves wrong. Every re-pinned
   assertion gets a comment naming the finding ID and the hand-computed arithmetic. List
   each in the work summary.
5. Targeted pytest during edits; pylint app/ --fail-on=E,F clean (no new warnings vs
   baseline); full pytest (-n 12 default) green as the per-commit final gate. Migrations
   (if any) round-trip upgrade->downgrade->upgrade cleanly; destructive changes get a Review
   docstring line and explicit developer approval before authoring.
6. Stay in scope. Out-of-scope issues, gold-plating opportunities, or refactors you noticed but did not
   perform MUST be flagged in the work summary with file:line and a one-sentence reason for not acting.
   Do not silently fold them in. Items that are not directly handled by a future commit need to be
   added to docs/audits/financial_calculations/remediation_follow_up.md. Any trivial items offer to
   address as a separate commit.
7. Do not push. After the work is green, present the work summary and ASK whether to commit
   and push to dev (this triggers CI; PR-to-main is required for promotion).

Work summary format (use these labels verbatim):
A. Verification: claims you re-verified; any drift found.
B. Files changed: list with one-line purpose each.
C. Tests added/modified: ID, name, hand-computed Decimal + arithmetic comment.
D. Re-pinned tests: finding ID + arithmetic per assertion, or "none".
E. Targeted-suite and full-suite final summary lines, verbatim from pytest.
F. Pylint final line, verbatim.
G. Migrations: upgrade->downgrade->upgrade result, or "n/a".
H. Invariants: which existing regression locks stayed green; which new locks added.
I. Discovered refinements folded in: each with rationale.
J. OUT OF SCOPE -- flagged, not fixed: file:line + reason per item + F-# entry in remediation_follow_up.md.
K. Open questions / assumptions made.
L. Proposed commit message (format: <type>(<scope>): <what> with the Co-Authored-By trailer
   per CLAUDE.md).
M. Ask: "Ready to commit and push to dev?"

Specific verification gates for this commit:
- Triggering a transfer-template regeneration that supersedes transfers emits one
  EVT_TRANSFER_HARD_DELETED per deletion (assert via log/event capture or via direct
  audit_log inspection if that is the project pattern).
- The orphan-verification check runs (assert by mocking or by inspecting the canonical
  service path).
- Shadow pair atomicity unchanged (no balance delta from this commit).
- `grep -n "db\.session\.delete(xfer)\|db\.session\.delete(transfer)" app/services/
  transfer_recurrence.py` is empty.

If anything is unclear, ASK.
```

---

### Commit 35 -- `docs(audit): correct comment/table drift (LOW-04, LOW-05, R-9, R-10)`

**Prereqs on dev:** none. **Closes:** LOW-04, LOW-05 (Q-26 sub-2 comment correction), R-9, R-10.
Q-26 sub-2's product question remains open and is NOT resolved.

```text
You are executing Commit 35 of the Shekel financial-calculation audit remediation in a
fresh session. Work in the project root on the dev branch.

Required reading -- in full:
- @docs/audits/financial_calculations/remediation_plan.md (Sections 0-7; Section 12 Open
  Questions for Q-26 sub-2; Section 9 "Commit 35" A-H)
- @CLAUDE.md
- @docs/coding-standards.md
- @app/models/user.py (~`:212-215`, the comment promising a bracket fallback the code
  does not implement)
- @app/services/retirement_gap_calculator.py (calculate_gap; confirms None -> no tax
  adjustment, no fallback)
- @docs/audits/financial_calculations/04_source_of_truth.md (D3 classification table that
  is missing the escrow_components.inflation_rate row -- LOW-04)
- @docs/audits/financial_calculations/09_open_questions.md (Q-26 / A-26 carried-tail
  contract -- LOW-05's product question)

Objective: doc-only changes. (a) Fix the user.py estimated_retirement_tax_rate comment to
say "NULL = no retirement-tax adjustment applied" (matches code; A-26's decided direction).
DO NOT build a fallback. (b) Add the missing
budget.escrow_components.inflation_rate -> AUTHORITATIVE row to 04_source_of_truth.md's D3
table (LOW-04). (c) Record R-9 and R-10 reconciliation notes (PA-08 carry-forward
scenario filter now present; PA-10/PA-11 single-producer tests exist but the cross-page
lock was absent until Commit 11) in 08_findings.md (addendum) or wherever the audit
captures R-notes. Q-26 sub-2's product decision (should a bracket fallback EVER exist)
remains an open question carried forward; this commit does NOT resolve it.

Production files this commit touches:
- app/models/user.py (comment)
- docs/audits/financial_calculations/04_source_of_truth.md (D3 table row)
- docs/audits/financial_calculations/08_findings.md (R-9/R-10 addendum; or wherever the
  audit's R-notes live)

Apply these rules (the plan's Section 1 is the authoritative version):
1. The plan's specification for this commit (Section 9, subsections A through H) is the
   floor, not the ceiling. If verification surfaces extra in-scope refinements, fold them
   in and explain in the work summary.
2. Trust-but-verify: re-grep every cited symbol; read every file you will change in full;
   confirm the audit/plan claim still holds against current code before editing. If reality
   has drifted, stop and report in the work summary before continuing.
3. No shortcuts, no band-aid fixes. Fix root causes. Decimal money from strings, IDs and
   semantic booleans for business logic (never name strings), DRY/SOLID, fully normalized
   schema, pythonic type-hinted code, specific exceptions, substantive docstrings, no
   Unicode em/en dashes (use -- or -).
4. Never modify a test to make it pass except for the documented exception in plan Section 1
   rule 2: tests pinning a shipping wrong number this finding proves wrong. Every re-pinned
   assertion gets a comment naming the finding ID and the hand-computed arithmetic. List
   each in the work summary.
5. Targeted pytest during edits; pylint app/ --fail-on=E,F clean (no new warnings vs
   baseline); full pytest (-n 12 default) green as the per-commit final gate. Migrations
   (if any) round-trip upgrade->downgrade->upgrade cleanly; destructive changes get a Review
   docstring line and explicit developer approval before authoring.
6. Stay in scope. Out-of-scope issues, gold-plating opportunities, or refactors you noticed but did not
   perform MUST be flagged in the work summary with file:line and a one-sentence reason for not acting.
   Do not silently fold them in. Items that are not directly handled by a future commit need to be
   added to docs/audits/financial_calculations/remediation_follow_up.md. Any trivial items offer to
   address as a separate commit.
7. Do not push. After the work is green, present the work summary and ASK whether to commit
   and push to dev (this triggers CI; PR-to-main is required for promotion).

Note for this commit: no source/test/migration changes here -- doc-only.

Work summary format (use these labels verbatim):
A. Verification: claims you re-verified; any drift found.
B. Files changed: list with one-line purpose each.
C. Tests added/modified: ID, name, hand-computed Decimal + arithmetic comment.
D. Re-pinned tests: finding ID + arithmetic per assertion, or "none".
E. Targeted-suite and full-suite final summary lines, verbatim from pytest.
F. Pylint final line, verbatim.
G. Migrations: upgrade->downgrade->upgrade result, or "n/a".
H. Invariants: which existing regression locks stayed green; which new locks added.
I. Discovered refinements folded in: each with rationale.
J. OUT OF SCOPE -- flagged, not fixed: file:line + reason per item + F-# entry in remediation_follow_up.md.
K. Open questions / assumptions made.
L. Proposed commit message (format: <type>(<scope>): <what> with the Co-Authored-By trailer
   per CLAUDE.md).
M. Ask: "Ready to commit and push to dev?"

Specific verification gates for this commit:
- `grep -n "fall back" app/models/user.py` is empty for the estimated_retirement_tax_rate
  comment block.
- D3 table includes the inflation_rate row (visual review; cite the line number).
- 08_findings.md R-9/R-10 notes present.
- Q-26 sub-2 is documented as still-open product question (the existing carried-tail
  contract); no fallback feature is built.

If anything is unclear, ASK.
```

---

### Commit 36 -- `test(arch): enforce no-Flask-in-services import linter (OPT-3, B6-01)`

**Prereqs on dev:** none (test-only; the boundary already holds today). **Closes:** OPT-3 / B6-01
(mechanical enforcement of the services boundary).

```text
You are executing Commit 36 of the Shekel financial-calculation audit remediation in a
fresh session. This commit is OPTIONAL per the plan's Section 5 (OPT-3). The developer
has opted in by running this prompt. Work in the project root on dev.

Required reading -- in full:
- @docs/audits/financial_calculations/remediation_plan.md (Sections 0-7; Section 5 OPT-3;
  Section 9 "Commit 36" A-H)
- @CLAUDE.md (Architecture: services never import flask objects)
- @docs/coding-standards.md, @docs/testing-standards.md
- @docs/audits/financial_calculations/06_dry_solid.md (B6-01 with the prose-vs-mechanical
  enforcement gap)

Objective: create tests/test_arch/test_services_no_flask.py that AST-scans every app/
services/*.py and fails on any import of flask, flask.request, flask.session,
flask.current_app, flask.g (the request-context global, NOT loop-variable name collisions
-- AST distinguishes), or flask.render_template. Allow db.session (SQLAlchemy, permitted
per the audit's verification). One mechanical test replaces 22 prose docstrings.

Production files this commit touches:
- tests/test_arch/test_services_no_flask.py (new)

Apply these rules (the plan's Section 1 is the authoritative version):
1. The plan's specification for this commit (Section 9, subsections A through H) is the
   floor, not the ceiling. If verification surfaces extra in-scope refinements, fold them
   in and explain in the work summary.
2. Trust-but-verify: re-grep every cited symbol; read every file you will change in full;
   confirm the audit/plan claim still holds against current code before editing. If reality
   has drifted, stop and report in the work summary before continuing.
3. No shortcuts, no band-aid fixes. Fix root causes. Decimal money from strings, IDs and
   semantic booleans for business logic (never name strings), DRY/SOLID, fully normalized
   schema, pythonic type-hinted code, specific exceptions, substantive docstrings, no
   Unicode em/en dashes (use -- or -).
4. Never modify a test to make it pass except for the documented exception in plan Section 1
   rule 2: tests pinning a shipping wrong number this finding proves wrong. Every re-pinned
   assertion gets a comment naming the finding ID and the hand-computed arithmetic. List
   each in the work summary.
5. Targeted pytest during edits; pylint app/ --fail-on=E,F clean (no new warnings vs
   baseline); full pytest (-n 12 default) green as the per-commit final gate. Migrations
   (if any) round-trip upgrade->downgrade->upgrade cleanly; destructive changes get a Review
   docstring line and explicit developer approval before authoring.
6. Stay in scope. Out-of-scope issues, gold-plating opportunities, or refactors you noticed but did not
   perform MUST be flagged in the work summary with file:line and a one-sentence reason for not acting.
   Do not silently fold them in. Items that are not directly handled by a future commit need to be
   added to docs/audits/financial_calculations/remediation_follow_up.md. Any trivial items offer to
   address as a separate commit.
7. Do not push. After the work is green, present the work summary and ASK whether to commit
   and push to dev (this triggers CI; PR-to-main is required for promotion).

Work summary format (use these labels verbatim):
A. Verification: claims you re-verified; any drift found.
B. Files changed: list with one-line purpose each.
C. Tests added/modified: ID, name, hand-computed Decimal + arithmetic comment.
D. Re-pinned tests: finding ID + arithmetic per assertion, or "none".
E. Targeted-suite and full-suite final summary lines, verbatim from pytest.
F. Pylint final line, verbatim.
G. Migrations: upgrade->downgrade->upgrade result, or "n/a".
H. Invariants: which existing regression locks stayed green; which new locks added.
I. Discovered refinements folded in: each with rationale.
J. OUT OF SCOPE -- flagged, not fixed: file:line + reason per item + F-# entry in remediation_follow_up.md.
K. Open questions / assumptions made.
L. Proposed commit message (format: <type>(<scope>): <what> with the Co-Authored-By trailer
   per CLAUDE.md).
M. Ask: "Ready to commit and push to dev?"

Specific verification gates for this commit:
- The test PASSES today (the boundary holds; the audit's grep was empty).
- A negative-case sub-test temporarily injects `from flask import request` into a
  scratch module under app/services/ AND asserts the linter detects it (then removes
  the injection); this proves the linter actually bites. Use a tmp_path fixture or a
  controlled fake -- never modify real services for the test.
- AST-based, not regex: the test must NOT flag loop variables named g (the audit found
  this false-positive class previously).

If anything is unclear, ASK.
```

---

### Commit 37 -- `chore(release): full gate + save remediation doc`

**Prereqs on dev:** every prior commit (1 through 36; OPT commits 22 and 36 included if run).
**Closes:** the remediation as a whole; the doc is already in the repo, this commit is the
acceptance gate.

```text
You are executing Commit 37 of the Shekel financial-calculation audit remediation, the
FINAL ACCEPTANCE GATE for the whole remediation, in a fresh session. Work in the project
root on the dev branch.

Required reading -- in full:
- @docs/audits/financial_calculations/remediation_plan.md (read the WHOLE plan end to end;
  this is your acceptance checklist)
- @CLAUDE.md (Definition of Done; Git Workflow)
- @docs/coding-standards.md, @docs/testing-standards.md
- @docs/audits/financial_calculations/08_findings.md (every finding must have its
  corresponding commit landed)
- @docs/audits/financial_calculations/05_symptoms.md (Section 10 walkthrough -- walk all
  five symptoms in the running app)
- @docs/audits/financial_calculations/remediation_commit_prompts.md (this file -- verify
  all 37 prompts have a corresponding commit on dev)

Objective: the final acceptance gate for the entire remediation. NO source/test/migration
changes are expected in this commit -- it is verification + (if drift detected) the
plan-doc save/update. If you discover a gap, STOP and report; do not paper over.

Gate checklist (every item must pass before the commit):
1. `python scripts/build_test_template.py` runs cleanly; the audit-trigger-count health
   check expects the post-Commit-12 trigger count.
2. `pytest` (full suite, default -n 12) ends in `N passed`, zero failed/errors/xfailed.
3. `pylint app/ --fail-on=E,F` clean; no new warnings vs the baseline.
4. Every migration touched by this remediation: `flask db upgrade` then `flask db
   downgrade` then `flask db upgrade` cleanly. List each migration and its round-trip
   result.
5. The cross-page balance-equality invariant (Commit 11) green.
6. The ARM-window stability lock (Commit 13) green.
7. CRIT-05 hard-delete blocking (Commit 21) green; data-loss path closed.
8. Hand-computed reconciliation appendix (plan Section 11): pick 10 pinned values at
   random across the test suite and confirm each resolves to the cited code with the
   arithmetic shown in the test comment (trust-but-verify the plan's own output).
9. Walk all five developer-reported symptoms in the running app per plan Section 10;
   report PASS/FAIL per symptom.
10. `git status` shows zero unintended files; the remediation_plan.md and
    remediation_commit_prompts.md are in docs/audits/financial_calculations/ and match
    the approved versions.
11. Q-26 sub-2 is still recorded as an open product question (the only carried open
    question after this remediation).

If any item fails, STOP and report -- do not commit a release gate that does not pass.

Apply these rules (the plan's Section 1 is the authoritative version):
1. The plan's specification for this commit (Section 9, subsections A through H) is the
   floor, not the ceiling. If verification surfaces extra in-scope refinements, fold them
   in and explain in the work summary.
2. Trust-but-verify: re-grep every cited symbol; read every file you will change in full;
   confirm the audit/plan claim still holds against current code before editing. If reality
   has drifted, stop and report in the work summary before continuing.
3. No shortcuts, no band-aid fixes. Fix root causes. Decimal money from strings, IDs and
   semantic booleans for business logic (never name strings), DRY/SOLID, fully normalized
   schema, pythonic type-hinted code, specific exceptions, substantive docstrings, no
   Unicode em/en dashes (use -- or -).
4. Never modify a test to make it pass except for the documented exception in plan Section 1
   rule 2: tests pinning a shipping wrong number this finding proves wrong. Every re-pinned
   assertion gets a comment naming the finding ID and the hand-computed arithmetic. List
   each in the work summary.
5. Targeted pytest during edits; pylint app/ --fail-on=E,F clean (no new warnings vs
   baseline); full pytest (-n 12 default) green as the per-commit final gate. Migrations
   (if any) round-trip upgrade->downgrade->upgrade cleanly; destructive changes get a Review
   docstring line and explicit developer approval before authoring.
6. Stay in scope. Out-of-scope issues, gold-plating opportunities, or refactors you noticed but did not
   perform MUST be flagged in the work summary with file:line and a one-sentence reason for not acting.
   Do not silently fold them in. Items that are not directly handled by a future commit need to be
   added to docs/audits/financial_calculations/remediation_follow_up.md. Any trivial items offer to
   address as a separate commit.
7. Do not push. After the work is green, present the work summary and ASK whether to commit
   and push to dev (this triggers CI; PR-to-main is required for promotion).

Note for this commit (CLAUDE.md rule 4 emphasis -- never ignore a problem): this is the
final acceptance gate. If any gate-checklist item fails, STOP and report; do not commit a
release gate that does not pass.

Work summary format (use these labels verbatim):
A. Verification: claims you re-verified; any drift found.
B. Files changed: list with one-line purpose each.
C. Tests added/modified: ID, name, hand-computed Decimal + arithmetic comment.
D. Re-pinned tests: finding ID + arithmetic per assertion, or "none".
E. Targeted-suite and full-suite final summary lines, verbatim from pytest.
F. Pylint final line, verbatim.
G. Migrations: upgrade->downgrade->upgrade result, or "n/a".
H. Invariants: which existing regression locks stayed green; which new locks added.
I. Discovered refinements folded in: each with rationale.
J. OUT OF SCOPE -- flagged, not fixed: file:line + reason per item + F-# entry in remediation_follow_up.md.
K. Open questions / assumptions made.
L. Proposed commit message (format: <type>(<scope>): <what> with the Co-Authored-By trailer
   per CLAUDE.md).
M. Ask: "Ready to commit and push to dev?"

Section H emphasis for this commit (the final acceptance gate): enumerate every
regression lock the remediation added and confirm each is green.

Proposed commit message: `chore(release): full remediation acceptance gate -- all 25
audit findings closed; cross-page and ARM-window invariants locked; doc preserved`.

Final ask: "All 25 findings closed; full suite green; invariants locked. The remediation
is complete pending your push to dev and PR-merge to main. Ready to commit and push?"

If anything is unclear, ASK.
```

---

## Final notes

- These prompts are the floor. Each session you run them in will read the canonical plan and may
  surface refinements. Trust the work summary's section J ("OUT OF SCOPE -- flagged, not fixed") to
  capture anything the agent saw but should not act on; act on those separately, with explicit
  scope.
- If you edit the remediation plan after starting execution, the prompts will pick up the new
  content automatically (they reference Section 9 by `@`-path), so you do not need to regenerate the
  prompts. If you renumber commits in the plan, the prompts need a corresponding renumber here.
- Sequential, atomic, suite-green: that is the discipline. The plan is the floor; the cross-page
  invariant (Commit 11) and the ARM-window stability lock (Commit 13) are the regression anchors
  that make sure no later commit silently regresses the criticals.

End of prompts.
