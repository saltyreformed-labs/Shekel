> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Phase 7 Execution Plan -- Test Coverage Gaps for Financial Assertions

Meta-plan for executing Phase 7 of the financial-calculation audit. This file is
the launch script: each session below is started by pasting its ready-to-paste
prompt (section 8) verbatim. This file is a planning artifact, not a phase
output and not source code. The phase output is `07_test_gaps.md`.

Authoritative spec: `financial_calculation_audit_plan.md` section 7 (lines
677-704) and section 10 (how to run). This file does not override the audit
plan; it sequences it and binds the trust-but-verify contract the developer
requires. Where the audit plan and this file appear to differ, the audit plan
wins and the divergence is a defect in this file to be reported, not worked
around.

## 0. Why Phase 7 is different now

Phases 3-6 asked "is the number wrong, where, and why." Phase 7 asks the
question that decides whether the developer can act on any of it without
shipping a new defect: **for each financial concept, does a test exist that
would fail if the number were wrong, and is that test asserting the right
number?** A calculation with no hand-verified pinned test is a calculation the
developer has no leverage to fix without breaking
(`financial_calculation_audit_plan.md:680-681`).

Four things make Phase 7 different from a generic coverage pass:

1. **A green test bar is not evidence of correctness, and Phase 7 must say so
   with citations.** `testing-standards.md` is explicit: "A test that does not
   verify behavior is worse than no test -- it creates false confidence," and
   "Financial assertions were computed by hand." Phases 3 and 5 proved that
   several producers compute a value the governing E-NN says is wrong (F-013,
   F-014, F-026, F-001/F-003/F-008/F-009, the symptom set). **A test that pins
   the divergent value is not coverage; it is anti-coverage** -- it will fail
   the moment the producer is corrected, and CLAUDE.md rule 5 forbids "fixing"
   the test. Phase 7 introduces an explicit verdict for exactly this:
   `PINNED-AGAINST-DIVERGENT-BEHAVIOR`. Treating such a test as "covered" would
   poison Phase 8 severity and is itself the trust-then-verify trap the audit
   exists to surface (audit-plan 10.8). This is the single most consequential
   Phase 7 classification.

2. **The register of what must be tested is already locked, not open.** Every
   `DIVERGE` finding in `03_consistency.md` (F-001..F-056), the five symptom
   regression targets named in the Phase 5 handoff (`05_symptoms.md:1714-1721`),
   and the per-D6/S6/B6 cross-site equivalence tests named in the Phase 6
   handoff (`06_dry_solid.md:2193-2206`) each define a specific assertion that
   *should* exist. Phase 7 does not re-derive what the correct behavior is --
   that is the governing E-NN's job and it is settled. Phase 7 determines, by
   reading the test suite, whether the assertion exists, whether it is pinned,
   and whether it pins the E-NN-consistent value; and where it is absent, it
   proposes the test **in the report only** (audit-plan 7 lines 696-703; hard
   rule 2: no test changes).

3. **The prior test-audit documents are out of scope and are not opened.**
   `docs/test_audit_report.md`, `docs/test_remediation_plan.md`, and
   `docs/test_audit_phase0_phase1.md` are dated 2026-03-22 -- before the
   Section 5/8 calculation work, the transfer rework verification, and this
   audit. They predate every finding Phase 7 builds on, and a stale pointer
   from them is a contamination risk, not a shortcut. Phase 7 derives every
   coverage and gap claim directly from the live `tests/` tree this session.
   The prior docs are neither cited nor relied upon, even as a lead list. (If
   a session is ever tempted to open one, that is scope drift -- the live grep
   is the only evidence Phase 7 accepts.)

4. **"No test exists" is an absence claim, and absence is proven mechanically
   or not at all.** The audit plan's "if you cannot cite it, you cannot claim
   it" (hard rule 4) applies symmetrically to absence: a gap claim requires the
   `grep` over `tests/` with its empty or non-matching result pasted. A gap
   asserted from memory is not a finding; it is an unverified guess and is
   marked so.

One tail item is carried, not resolved here: the A-26
`auth.user_settings.estimated_retirement_tax_rate` NULL-semantics question
remains out of scope (it is a developer-adjudicated open question, not a test
gap with a known correct assertion) and is carried to Phase 9 unchanged
(`05_symptoms.md:1731-1740`, `06_dry_solid.md:2218-2225`). Phase 7 records its
test state as `BLOCKED-ON-OPEN-QUESTION` ("no pinned assertion possible until
the NULL-contract is decided") and does not propose a value-pinned test for it.

## 1. Inherited spine (a falsifiable register, not a checklist)

Phase 7 inherits three concrete registers. Each entry is re-proven against the
live `tests/` tree this phase (section 2 contract). A register entry the audit
assumes untested but that in fact has a pinned, E-NN-consistent test is a
Phase 7 finding against the assumption; a register entry the audit assumes
tested whose test is loose or pins the divergent value is the higher-stakes
finding.

| Source register | Where | What Phase 7 must verify per entry |
| --- | --- | --- |
| The 47-concept controlled vocabulary | `02_concepts.md` (47 `## Concept:` sections) + audit-plan Appendix A | every concept's canonical producer has >=1 pinned-value test, and the pinned value is E-NN-consistent |
| The 56 Phase 3 findings | `03_consistency.md` F-001..F-056; `DIVERGE` verdicts especially | for each `DIVERGE`, does a test exist that would have failed on the divergence? |
| The 5 symptom regression targets | `05_symptoms.md:1714-1721` | cross-page balance equality (#1,#5); cross-surface+cross-date payment equality in the ARM window (#2,#4); strict principal decrease per settled transfer (#3); plus each symptom's named falsification / negative-control test |
| The 23 Phase 6 structural findings | `06_dry_solid.md:2193-2206` | each D6- consolidation's implied cross-site equivalence test; S6-03 dispatcher-equivalence; B6-01/B6-02 mechanical `flask`/`Transfer`-absence test |

The cross-page consistency meta-gap is called out separately and explicitly,
per `financial_calculation_audit_plan.md:700-703`: the single fixture that sets
up an account with known transactions and asserts every page-facing service
produces the same balance for the same period. This gap is recorded even if
every individual per-concept test exists, because it is the test the developer
most needs and almost certainly does not have (it is the structural statement
of symptoms #1 and #5).

This register is the **starting set, not the closing set.** The census in
sections 4 (P7-a..P7-d) must also surface concepts whose only tests are loose,
and edge cases the inventory flagged that no test exercises, even when no
finding pre-named them.

## 2. The trust-but-verify contract (binds every Phase 7 session)

1. **Every coverage claim is grepped and Read this session.** "Concept X is
   tested by `test_Y`" requires the `grep` that found `test_Y` and the test
   body Read this session with the asserting line(s) quoted into
   `07_test_gaps.md`. A coverage claim with no quoted assertion is not a
   finding; it is incomplete and is marked so.
2. **Every absence claim is grepped this session.** "No pinned test exists for
   concept X" requires the `grep`/`glob` over `tests/` (the producer function
   name, the concept's route, the asserted-magnitude pattern) with the empty or
   non-matching result pasted. Absence is mechanical or it is not claimed.
3. **Pinned vs loose is decided by reading the assertion, never the test
   name.** A test named `test_monthly_payment_exact` that asserts
   `assert payment > 0` is LOOSE. Pinned = exact `Decimal` (or `==` against a
   hand-computed scalar / string-constructed `Decimal`). Loose = `>`, `>=`,
   `is not None`, `pytest.approx`, bool, or an assertion on count/shape only.
   The quoted assertion line is the evidence; the classification without it is
   not a finding.
4. **Coverage is cross-checked against the Phase 3/5 verdict for that
   concept.** A pinned test whose asserted value contradicts the governing
   E-NN / the canonical primary path that Phase 3 or Phase 5 established is
   classified `PINNED-AGAINST-DIVERGENT-BEHAVIOR`, not `COVERED`. This requires
   re-reading the relevant F-NN verdict and the test's expected value in the
   same session and showing both numbers. This is the load-bearing Phase 7
   rule: it is how the audit refuses to let a green bar launder a known wrong
   number. Such tests are **flagged only** -- the test file is never opened for
   modification, annotation, or proposal-in-place; it is recorded in the
   anti-coverage roll-up for the developer and Phase 8 to act on.
5. **The prior test-audit docs are not opened.** No claim in `07_test_gaps.md`
   is sourced from `test_audit_report.md` / `test_remediation_plan.md` /
   `test_audit_phase0_phase1.md`. Every coverage and gap claim is derived from
   the live `tests/` tree this session. Opening a prior test-audit doc is scope
   drift (section 0 item 3).
6. **No test is written, modified, run, or deleted.** Phase 7 proposes tests
   *in the report only* (audit-plan 7 lines 696-703; hard rule 2). A proposed
   test includes the fixture sketch, the exact assertion, and the hand-computed
   expected value with the arithmetic shown -- as prose/pseudocode in
   `07_test_gaps.md`, never as a file under `tests/`. The suite is not
   executed; coverage is read from source, not from a coverage run (running the
   suite is out of scope). A test file accidentally created or edited during a
   session is a finding artifact to revert (audit-plan 10.6).
7. **Read-only. Plan permission mode.** No app run, no code/test/migration/
   template/JS edit, no `pytest` invocation. Output is `07_test_gaps.md` only;
   `git status` must show only `docs/audits/financial_calculations/` at every
   session end.
8. **Explore subagent for the broad test-tree sweeps is mandatory, not
   optional** (`financial_calculation_audit_plan.md:990-994`, section 10.1c
   names Phase 7 test-coverage scan as an Explore workload). The per-concept /
   per-finding sweeps over `tests/` in sections 4 run inside Explore so raw
   test-file contents stay out of the main session; the main session Reads the
   specific asserting lines Explore returns and does the pinned/loose and
   E-NN-consistency classification itself (it cannot delegate the judgment in
   contract items 3 and 4).

## 3. Per-concept and per-gap schema

`07_test_gaps.md` has three top-level parts: Part 7.A (per-concept coverage
census), Part 7.B (divergence-catching audit + symptom regression targets +
the cross-page meta-gap), Part 7.C (proposed tests, report-only).

### 3.1 Per-concept coverage record (Part 7.A), one per controlled-vocabulary concept

- **Concept** -- the token from the controlled vocabulary.
- **Canonical producer** -- the primary path from `02_concepts.md` (or
  `PRIMARY PATH: UNKNOWN` carried verbatim, which is itself a coverage problem
  -- you cannot pin a test to an undesignated producer; record it as such).
- **Pinned-value tests** -- each as `tests/.../test_file.py::test_name`, the
  asserted value, the producer it exercises, the assertion line quoted this
  session.
- **Relationship tests** -- tests asserting an inter-concept invariant
  (`net == gross - taxes - deductions`; `balance[p]-balance[p-1] ==
  subtotal.net`), quoted.
- **Pinned / loose classification** -- per test, with the quoted assertion as
  the evidence (contract item 3).
- **E-NN-consistency check** -- for any concept that Phase 3/5 verdicted
  `DIVERGE`, does the pinned value match the E-NN-consistent value or the
  divergent one? Show both numbers and the F-NN citation (contract item 4).
- **Consistency-invariant test present?** -- is there any test asserting this
  concept is equal across its multiple producers / pages? (Usually "no" -- that
  is the point.)
- **Edge cases untested** -- edges the inventory (`01_inventory.md`) or Phase 3
  flagged (zero, negative/overdraft, anchor-None, ARM in-window boundary,
  credit status, settled status, empty period) with no test exercising the
  specific edge behavior (`testing-standards.md` edge-case rule).
- **Coverage verdict** -- exactly one of: `COVERED` (>=1 pinned test, value
  E-NN-consistent, key edges tested); `LOOSE-ONLY`; `NO-PINNED-TEST`;
  `PINNED-AGAINST-DIVERGENT-BEHAVIOR`; `PRODUCER-UNKNOWN-CANNOT-PIN`;
  `BLOCKED-ON-OPEN-QUESTION` (e.g. A-26). Every non-`COVERED` verdict is a
  Phase 7 gap finding.
- **Independent note** -- one line: which P7 session derived this, and any edge
  the census surfaced that no register entry pre-named.

### 3.2 Per-divergence test-gap record (Part 7.B), one per `DIVERGE` finding + per symptom target + per D6/S6/B6

- **Finding / target** -- F-NN (or symptom #N, or D6-/S6-/B6-NN) and its
  one-line divergence statement, re-read this session.
- **Catching test search** -- the grep run over `tests/` for a test that
  exercises both diverging paths / asserts the equality the finding says is
  violated; the result pasted.
- **Would any existing test have caught it?** -- YES (cite the test and quote
  the assertion that would have failed) or NO (the grep result proving
  absence). For YES, also state whether that test currently passes against the
  divergent code -- if it passes, it does not actually assert the invariant and
  the YES is downgraded to NO with the reason.
- **Proposed test pointer** -- the Part 7.C entry ID that specifies the test
  that would catch it (report-only).
- **Blast radius** -- one sentence: which displayed figure ships wrong
  undetected because this gap exists, cross-linked to the symptom / C3 CRITICAL
  pre-list entry (`03_consistency.md:6062+`) it corresponds to.

### 3.3 Proposed-test record (Part 7.C), report-only, never written

- **ID** -- `PT-NN`.
- **Catches** -- the F-NN / symptom / D6- it would have caught.
- **Fixture sketch** -- the minimal known-input setup in prose (account type,
  anchor, transactions with statuses/periods, loan params), not code.
- **Exact assertion** -- the equality/value to assert, stated precisely.
- **Hand-computed expected value** -- the arithmetic that produces the pinned
  number, every intermediate `Decimal` step shown (`testing-standards.md`
  service-test rule: include the arithmetic). For a divergence-consistent
  value, the value is the E-NN-correct one, explicitly NOT the current
  divergent output.
- **Why it is not code** -- one line restating that this is a proposal; writing
  it is deferred to the post-audit remediation session (hard rule 2,
  audit-plan 10.6).

## 4. Sessions

One concern-family per session. `/clear` between sessions. Required reading is
line-range-bounded to prevent the infinite-exploration / context-blowout
failure mode. Read the live test function in full before classifying it
(CLAUDE.md rule 2; audit-plan hard rule 3 applied to test code: do not infer
pinned-ness from a test name or a 3-line excerpt). The large audit docs
(`01`/`02`/`03`/`05`/`06`) are **never** read whole and never referenced with
`@`; sessions grep them for the named anchors and Read only the bounded ranges
below. The three prior test-audit docs are **never opened** (section 0 item 3).

The 47-concept census is fanned out across four concern-family sessions
(P7-a..P7-d) so no single session reads enough concept sections + Explore-swept
test hits to risk context blowout. Then P7-e builds the divergence/symptom/
meta-gap analysis and the report-only proposals; P7-f is the gate.

### P7-a -- Census slice 1: the balance / anchor / cross-page family (8 concepts)

Goal: the coverage census for the eight concepts that carry the cross-page
balance symptoms (#1, #5). Highest-stakes slice -- first.

Concepts: `checking_balance`, `projected_end_balance`, `account_balance`,
`period_subtotal`, `chart_balance_series`, `net_worth`, `savings_total`,
`debt_total`.

Required reading (bounded):
- `financial_calculation_audit_plan.md:677-704`.
- `02_concepts.md`: grep `^## Concept:` for line ranges; Read only the section
  for each of the 8.
- `03_consistency.md`: grep `^### Finding F-0` / `^## Finding F-0`; Read only
  F-001..F-011 and their `Verdict:` lines; Read `:6062-6075` (C3).
- `05_symptoms.md:1701-1763` (Phase 5 handoff + per-symptom verification item
  5 for #1/#5).
- `06_dry_solid.md:2191-2206` (Phase 6 -> Phase 7 handoff).
- Live `tests/` via Explore (one invocation per producer family, thoroughness
  `very thorough`): `calculate_balances`,
  `calculate_balances_with_amortization`, `calculate_balances_with_interest`,
  `_sum_remaining`, `_sum_all`, `_entry_aware_amount`, `_compute_net_worth`,
  the grid / `/savings` / `/accounts` / chart route tests. Main session Reads
  each asserting line and classifies.

Stop condition: Part 7.A records for the 8 slice-1 concepts written, each with
every section-3.1 element, the asserting line quoted this session, one verdict;
session ends.

### P7-b -- Census slice 2: the loan / debt family (13 concepts)

Goal: the coverage census for the loan concepts carrying symptoms #2/#3/#4.

Concepts: `monthly_payment`, `loan_principal_real`, `loan_principal_stored`,
`loan_principal_displayed`, `principal_paid_per_period`,
`interest_paid_per_period`, `escrow_per_period`, `total_interest`,
`interest_saved`, `months_saved`, `payoff_date`, `loan_remaining_months`,
`dti_ratio`.

Required reading (bounded):
- `financial_calculation_audit_plan.md:677-704`.
- `02_concepts.md`: grep `^## Concept:`; Read only each of the 13 sections.
- `03_consistency.md`: Read only F-013..F-026 + their `Verdict:` lines; Read
  `:6062-6075` (C3).
- `05_symptoms.md:1701-1763` (Phase 5 handoff; symptoms #2/#3/#4 item 5).
- `06_dry_solid.md:2191-2206`.
- Live `tests/` via Explore (one per producer family): `calculate_monthly_payment`,
  `get_loan_projection`, `generate_schedule`, `calculate_summary`,
  `calculate_remaining_months`, `_compute_debt_summary`,
  `_derive_summary_metrics`, the `/accounts/<id>/loan` and debt-strategy route
  tests, `test_amortization_engine.py`, `test_balance_calculator_debt.py`.
  Main session Reads each asserting line and classifies, with special weight on
  the E-NN-consistency check (these concepts carry the proven `DIVERGE`s).

Stop condition: Part 7.A records for the 13 slice-2 concepts written; session
ends.

### P7-c -- Census slice 3: the income / tax / paycheck family (8 concepts)

Concepts: `paycheck_gross`, `paycheck_net`, `taxable_income`, `federal_tax`,
`state_tax`, `fica`, `pre_tax_deduction`, `post_tax_deduction`.

Required reading (bounded):
- `financial_calculation_audit_plan.md:677-704`.
- `02_concepts.md`: grep `^## Concept:`; Read only each of the 8 sections.
- `03_consistency.md`: Read only F-032..F-040 + their `Verdict:` lines.
- `00_priors.md:214-385` (E-20 calibration + the standards-derived E-10..E-17
  -- the Decimal-from-string / ID-based idioms a pinned tax test must itself
  obey, and the relationship invariants `net = gross - taxes - deductions`).
- Live `tests/` via Explore (one per producer family): `calculate_paycheck`,
  `calculate_federal_withholding`, `calculate_state_tax`, `calculate_fica`,
  `_calculate_deductions`, the calibration path, `test_paycheck_calculator.py`,
  `test_tax_calculator.py`, `test_calibration_service.py`. Main session Reads
  each asserting line and classifies; record the relationship-invariant tests
  explicitly.

Stop condition: Part 7.A records for the 8 slice-3 concepts written; session
ends.

### P7-d -- Census slice 4: the growth / retirement / transfer / goal / year-summary family (18 concepts)

Concepts: `apy_interest`, `growth`, `employer_contribution`,
`contribution_limit_remaining`, `ytd_contributions`, `transfer_amount`,
`transfer_amount_computed`, `effective_amount`, `goal_progress`,
`emergency_fund_coverage_months`, `cash_runway_days`, `pension_benefit_annual`,
`pension_benefit_monthly`, `year_summary_jan1_balance`,
`year_summary_dec31_balance`, `year_summary_principal_paid`,
`year_summary_growth`, `year_summary_employer_total`, plus any concept slices
1-3 deferred.

Required reading (bounded):
- `financial_calculation_audit_plan.md:677-704`.
- `02_concepts.md`: grep `^## Concept:`; Read only each of the 18 sections.
- `03_consistency.md`: Read only F-027..F-031, F-041..F-056 + their `Verdict:`
  lines; Read `:6062-6075` (C3).
- `00_priors.md:286-326` (E-26 rounding, E-28 the 0-vs-NULL idiom relevant to
  `contribution_limit_remaining`); `:184-197` (E-18, for `effective_amount` /
  loan-funded transfer interaction).
- Live `tests/` via Explore (one per producer family): `calculate_interest`,
  `growth_engine.project_balance`, `calculate_employer_contribution`,
  `effective_amount` property tests, transfer-amount tests,
  goal/EF/cash-runway producers, `pension_calculator`, the
  `year_end_summary_service` year-summary producers. Main session Reads each
  asserting line and classifies.

Stop condition: Part 7.A records for all slice-4 concepts written; after this
session **all 47 controlled-vocabulary concepts have a Part 7.A verdict**;
session ends.

### P7-e -- Divergence-catching audit, symptom regression targets, cross-page meta-gap, report-only proposals

Goal: for every `DIVERGE` finding, the five symptom targets, and the Phase 6
equivalence-test implications, determine by reading the suite whether a
catching test exists; build Part 7.B and the report-only Part 7.C.

Required reading (bounded):
- `financial_calculation_audit_plan.md:696-704` (the propose-a-test and
  cross-page-fixture clauses specifically).
- `03_consistency.md`: grep `Verdict:`, enumerate the DIVERGE set, Read only
  those findings' divergence paragraphs; Read `:6049-6075` (C2 + C3).
- `05_symptoms.md:1714-1721` (the five regression targets + each symptom's
  named falsification / negative-control test).
- `06_dry_solid.md:2193-2206` (the per-D6/S6/B6 implied equivalence tests).
- The full Part 7.A already written to `07_test_gaps.md` (recovery state; read
  it, do not re-derive it -- a concept already verdicted `NO-PINNED-TEST` in
  Part 7.A feeds Part 7.B directly).
- Live `tests/` via Explore: for each DIVERGE finding, the search for a test
  that exercises both diverging paths or asserts the violated equality; for the
  cross-page meta-gap, the search for any existing fixture that asserts one
  balance across grid + `/savings` + `/accounts` for the same period (the
  expected result is empty -- paste it).

Stop condition: Part 7.B (every DIVERGE finding + 5 symptom targets + the
Phase 6 equivalence implications + the explicit cross-page meta-gap, each with
the catching-test grep result) and Part 7.C (the `PT-NN` proposed tests,
report-only, each with the hand-computed expected value) written; session ends.

### P7-f -- Verification and consolidation gate (trust-but-verify capstone)

No new coverage analysis. Verify and consolidate.

Tasks:
1. **Spot-check:** choose >= 15 cited claims at random across Part 7.A / 7.B /
   7.C -- a mix of `COVERED` verdicts (re-open the test, re-confirm the
   assertion is pinned and E-NN-consistent), `NO-PINNED-TEST` verdicts
   (re-run the absence grep), and `PINNED-AGAINST-DIVERGENT-BEHAVIOR` verdicts
   (re-confirm both numbers). Show the table and pass count. Threshold 100%;
   any miss reopens that finding's session before the gate can pass.
2. **Concept-completeness reconciliation:** confirm all 47
   controlled-vocabulary concepts have a Part 7.A record with a verdict; no
   concept silently dropped; every `PRIMARY PATH: UNKNOWN` concept recorded as
   `PRODUCER-UNKNOWN-CANNOT-PIN`, not skipped.
3. **Divergence-completeness reconciliation:** confirm every `DIVERGE` finding
   in `03_consistency.md` has a Part 7.B entry; the 5 symptom regression
   targets and the explicit cross-page meta-gap present; the Phase 6
   D6-/S6-/B6- equivalence-test implications each addressed or explicitly
   recorded as already-covered with the test cited.
4. **Anti-coverage roll-up:** one table of every
   `PINNED-AGAINST-DIVERGENT-BEHAVIOR` finding -- the tests that will break
   when the code is correctly fixed and that CLAUDE.md rule 5 forbids "fixing."
   Flag only; the test files were never opened for modification. This table is
   the single most important Phase 7 output for the developer and Phase 8.
5. **Acceptance gate** (section 5), each criterion with evidence/verdict.
6. **Handoff:** what Phase 8 (findings -- each gap feeds a finding; severity
   assigned there, not here; the anti-coverage table is a CRITICAL input) and
   Phase 9 (open questions -- the A-26 tail; any concept verdicted
   `BLOCKED-ON-OPEN-QUESTION`) inherit. Carry the A-26
   `estimated_retirement_tax_rate` tail forward unchanged.
7. `git status` pasted, showing only `docs/audits/financial_calculations/`;
   confirm no file under `tests/` was created/modified/deleted and `pytest` was
   never invoked.

Stop condition: gate section appended to `07_test_gaps.md`; "Phase 7 complete"
recorded with the G1-G9 roll-up, or, if any gate fails, the failing criterion
and the session to reopen named, and the session stops without declaring
completion.

## 5. Phase 7 acceptance gate (mirrors Phase 5 / Phase 6 section 5)

Phase 7 is complete only when all hold, each with shown evidence:

- **G1** `07_test_gaps.md` exists, non-empty; three parts (7.A per-concept
  census, 7.B divergence/symptom/meta-gap, 7.C report-only proposed tests),
  every record carrying every section-3 element.
- **G2** Every coverage claim cites a `tests/...::test` Read this session with
  the asserting line quoted; every absence claim cites the `grep` over
  `tests/` with the empty/non-matching result pasted. No claim sourced from a
  prior test-audit doc (those are never opened).
- **G3** Every test classified pinned or loose carries the quoted assertion
  line as the evidence; no classification rests on a test name.
- **G4** Every concept Phase 3/5 verdicted `DIVERGE` whose test pins the
  divergent value is verdicted `PINNED-AGAINST-DIVERGENT-BEHAVIOR` with both
  numbers and the F-NN citation shown; the anti-coverage roll-up table exists
  and is flag-only (no test file opened for modification).
- **G5** Spot-check >= 15 sites (mixed verdict types), 100% resolve; table and
  count shown.
- **G6** All 47 controlled-vocabulary concepts have a Part 7.A verdict; every
  `DIVERGE` finding + the 5 symptom targets + the explicit cross-page meta-gap
  + the Phase 6 equivalence implications have a Part 7.B entry; nothing
  silently dropped.
- **G7** Every proposed test (Part 7.C) is report-only with a hand-computed
  expected value and the arithmetic shown, and explicitly pins the
  E-NN-correct value (not the current divergent output); no test file was
  created/modified/deleted and `pytest` was never run (or, if a test artifact
  was produced, it was reverted and recorded per audit-plan 10.6).
- **G8** No new auditor-invented "obvious" expectation added to
  `09_open_questions.md`; the A-26 tail carried unchanged; concepts blocked on
  an open question recorded as `BLOCKED-ON-OPEN-QUESTION`, not guessed.
- **G9** `git status` shows only `docs/audits/financial_calculations/` files
  changed; no source, test, migration, template, or JS file touched.

## 6. Anti-shortcut prompt (paste at the top of every Phase 7 session)

> This session is part of a read-only audit running in Claude Code's `plan`
> permission mode. Document findings in `07_test_gaps.md` with file and line
> citations to the actual test source, Read or grepped this session. Read the
> relevant test function fully before classifying it; pinned-vs-loose is
> decided by the quoted assertion line, never the test name. Verify every
> "concept X is tested" claim by grepping `tests/` and quoting the assertion;
> verify every "no test exists" claim by pasting the empty/non-matching grep.
> A pinned test whose value contradicts the governing E-NN / the Phase 3-5
> verdict for that concept is `PINNED-AGAINST-DIVERGENT-BEHAVIOR`, not covered
> -- show both numbers and flag it only; do not open that test file to modify
> or annotate it. Do not open or cite the prior test-audit docs
> (`test_audit_report.md` / `test_remediation_plan.md` /
> `test_audit_phase0_phase1.md`) -- derive every claim from the live `tests/`
> tree. Never write, modify, run, or delete a test; propose tests in the
> report only, with the hand-computed expected value and the arithmetic shown,
> pinning the E-NN-correct value. Use the Explore subagent for the
> repository-wide `tests/` sweeps so raw test contents stay out of this
> session; do the pinned/loose and E-NN-consistency judgment yourself.
> Findings go into `07_test_gaps.md`; source files, tests, and migrations
> remain untouched. Stay within the assigned session's concept/finding scope.

## 7. Failure modes and remedies

- Kitchen-sink session -> one concern-family per session; `/clear` between.
- Infinite exploration -> the bounded reading lists in section 4 and the
  mandatory Explore sweeps over `tests/`; do not widen without recording why.
- Trust-then-verify gap -> section 2 contract; the P7-f spot-check is mandatory.
- Name-trust -> pinned/loose proven from the quoted assertion at current
  source; a test name is a prompt to Read the body, not a verdict.
- Green-bar laundering -> the `PINNED-AGAINST-DIVERGENT-BEHAVIOR` verdict and
  the P7-f anti-coverage roll-up exist precisely to stop a known-wrong number
  from being recorded as covered; this is the single most consequential Phase 7
  classification and a miss here poisons Phase 8.
- Stale-doc contamination -> the prior test-audit docs are never opened; if a
  session opens one, that is scope drift; stop and re-prompt.
- Refactor / test-writing temptation -> Phase 7 proposes in prose only; a test
  file produced during a session is reverted and recorded as a proposal
  (audit-plan 10.6/10.7). `pytest` is never invoked.
- Scope drift -> if a session starts writing tests or running the suite, stop;
  the deliverable is the coverage map and the report-only proposals, not a
  test PR.

## 8. Ready-to-paste session prompts

Run strictly in order (P7-a -> P7-b -> P7-c -> P7-d -> P7-e -> P7-f), each in
its own session started with `claude --permission-mode plan`, with `/clear`
between. Do not use `@` on the large audit docs (`00`/`01`/`02`/`03`/`05`/
`06`/`09`) -- `@` reads the whole file and blows context; the prompts instruct
ranged Reads and greps instead. Each session's recovery state is the
accumulated `07_test_gaps.md` (audit-plan 10.5).

The anti-shortcut preamble below (the section 6 paragraph) prefixes every
prompt verbatim; it is written once here and referenced as "[anti-shortcut
preamble]" in P7-b..P7-f to keep this file readable. At paste time, expand it.

### Prompt P7-a (census slice 1: balance / anchor / cross-page family)

```text
This session is part of a read-only audit running in Claude Code's `plan`
permission mode. Document findings in
docs/audits/financial_calculations/07_test_gaps.md with file and line
citations to the actual test source, Read or grepped this session. Read the
relevant test function fully before classifying it; pinned-vs-loose is decided
by the quoted assertion line, never the test name. Verify every "concept X is
tested" claim by grepping tests/ and quoting the assertion; verify every "no
test exists" claim by pasting the empty/non-matching grep. A pinned test whose
value contradicts the governing E-NN / the Phase 3-5 verdict for that concept
is PINNED-AGAINST-DIVERGENT-BEHAVIOR, not covered -- show both numbers and flag
only; do not open that test file to modify or annotate it. Do not open or cite
test_audit_report.md / test_remediation_plan.md / test_audit_phase0_phase1.md
-- derive every claim from the live tests/ tree. Never write, modify, run, or
delete a test; propose tests in the report only. Use the Explore subagent for
the tests/ sweeps. Findings go into 07_test_gaps.md; source/tests/migrations
untouched.

This is Phase 7 session P7-a. Follow phase7_plan.md section 4 (P7-a), the
trust-but-verify contract in section 2, and the per-concept schema in section
3.1. Scope: the per-concept coverage census for the 8 balance/anchor/
cross-page concepts only: checking_balance, projected_end_balance,
account_balance, period_subtotal, chart_balance_series, net_worth,
savings_total, debt_total.

Bounded reading (Read exactly these ranges; do not widen without recording
why):
- docs/audits/financial_calculations/financial_calculation_audit_plan.md lines
  677-704.
- docs/audits/financial_calculations/02_concepts.md: grep "^## Concept:" for
  line ranges, Read only the section for each of the 8 concepts.
- docs/audits/financial_calculations/03_consistency.md: grep "^### Finding F-0"
  / "^## Finding F-0", Read only F-001..F-011 and their Verdict lines; Read
  lines 6062-6075 (C3 CRITICAL pre-list).
- docs/audits/financial_calculations/05_symptoms.md lines 1701-1763.
- docs/audits/financial_calculations/06_dry_solid.md lines 2191-2206.
- Live tests/ via Explore (one invocation per producer family, thoroughness
  very thorough): calculate_balances, calculate_balances_with_amortization,
  calculate_balances_with_interest, _sum_remaining, _sum_all,
  _entry_aware_amount, _compute_net_worth, the grid//savings//accounts/chart
  route tests. The main session Reads each asserting line Explore returns and
  does the pinned/loose + E-NN-consistency classification itself.

Produce in 07_test_gaps.md a "Part 7.A -- per-concept coverage census (slice
1)" section: one record per concept with every section-3.1 element, the
asserting line quoted this session, and exactly one coverage verdict (COVERED /
LOOSE-ONLY / NO-PINNED-TEST / PINNED-AGAINST-DIVERGENT-BEHAVIOR /
PRODUCER-UNKNOWN-CANNOT-PIN / BLOCKED-ON-OPEN-QUESTION). Do not run the app or
pytest. Do not modify code or tests. End by writing the section and stopping;
paste `git status` confirming only docs/audits/financial_calculations/ changed.
```

### Prompt P7-b (census slice 2: loan / debt family)

```text
[anti-shortcut preamble]

This is Phase 7 session P7-b. Follow phase7_plan.md section 4 (P7-b), the
trust-but-verify contract in section 2, and the per-concept schema in section
3.1. Scope: the per-concept coverage census for the 13 loan/debt concepts:
monthly_payment, loan_principal_real, loan_principal_stored,
loan_principal_displayed, principal_paid_per_period, interest_paid_per_period,
escrow_per_period, total_interest, interest_saved, months_saved, payoff_date,
loan_remaining_months, dti_ratio. These carry the proven DIVERGEs (symptoms
#2/#3/#4) -- weight the E-NN-consistency check heavily.

Bounded reading:
- financial_calculation_audit_plan.md lines 677-704.
- 02_concepts.md: grep "^## Concept:", Read only each of the 13 sections.
- 03_consistency.md: Read only F-013..F-026 and their Verdict lines; Read lines
  6062-6075 (C3).
- 05_symptoms.md lines 1701-1763 (symptoms #2/#3/#4 item 5).
- 06_dry_solid.md lines 2191-2206.
- Live tests/ via Explore (one per producer family): calculate_monthly_payment,
  get_loan_projection, generate_schedule, calculate_summary,
  calculate_remaining_months, _compute_debt_summary, _derive_summary_metrics,
  the /accounts/<id>/loan and debt-strategy route tests,
  test_amortization_engine.py, test_balance_calculator_debt.py. Main session
  Reads each asserting line and classifies.

Produce in 07_test_gaps.md a "Part 7.A -- per-concept coverage census (slice
2)" section with one record per concept (every section-3.1 element, asserting
line quoted, one verdict). Do not run the app or pytest. Do not modify code or
tests. End by writing the section and stopping; paste `git status`.
```

### Prompt P7-c (census slice 3: income / tax / paycheck family)

```text
[anti-shortcut preamble]

This is Phase 7 session P7-c. Follow phase7_plan.md section 4 (P7-c), the
contract in section 2, and the schema in section 3.1. Scope: the per-concept
coverage census for the 8 income/tax/paycheck concepts: paycheck_gross,
paycheck_net, taxable_income, federal_tax, state_tax, fica, pre_tax_deduction,
post_tax_deduction. Record the relationship-invariant tests
(net = gross - taxes - deductions) explicitly.

Bounded reading:
- financial_calculation_audit_plan.md lines 677-704.
- 02_concepts.md: grep "^## Concept:", Read only each of the 8 sections.
- 03_consistency.md: Read only F-032..F-040 and their Verdict lines.
- 00_priors.md lines 214-385 (E-20 calibration; E-10..E-17 the idioms a pinned
  tax test must itself obey; the net = gross - taxes - deductions invariant).
- Live tests/ via Explore (one per producer family): calculate_paycheck,
  calculate_federal_withholding, calculate_state_tax, calculate_fica,
  _calculate_deductions, the calibration path, test_paycheck_calculator.py,
  test_tax_calculator.py, test_calibration_service.py. Main session Reads each
  asserting line and classifies.

Produce in 07_test_gaps.md a "Part 7.A -- per-concept coverage census (slice
3)" section, one record per concept, every section-3.1 element, one verdict.
Do not run the app or pytest. Do not modify code or tests. End by writing the
section and stopping; paste `git status`.
```

### Prompt P7-d (census slice 4: growth / retirement / transfer / goal / year-summary family)

```text
[anti-shortcut preamble]

This is Phase 7 session P7-d. Follow phase7_plan.md section 4 (P7-d), the
contract in section 2, and the schema in section 3.1. Scope: the per-concept
coverage census for the 18 remaining concepts: apy_interest, growth,
employer_contribution, contribution_limit_remaining, ytd_contributions,
transfer_amount, transfer_amount_computed, effective_amount, goal_progress,
emergency_fund_coverage_months, cash_runway_days, pension_benefit_annual,
pension_benefit_monthly, year_summary_jan1_balance, year_summary_dec31_balance,
year_summary_principal_paid, year_summary_growth, year_summary_employer_total,
plus any concept slices 1-3 deferred. After this session all 47
controlled-vocabulary concepts must have a Part 7.A verdict.

Bounded reading:
- financial_calculation_audit_plan.md lines 677-704.
- 02_concepts.md: grep "^## Concept:", Read only each of the 18 sections.
- 03_consistency.md: Read only F-027..F-031, F-041..F-056 and their Verdict
  lines; Read lines 6062-6075 (C3).
- 00_priors.md lines 286-326 (E-26 rounding; E-28 the 0-vs-NULL idiom) and
  184-197 (E-18, for effective_amount / loan-funded transfer interaction).
- Live tests/ via Explore (one per producer family): calculate_interest,
  growth_engine.project_balance, calculate_employer_contribution, the
  effective_amount property tests, transfer-amount tests, the goal/EF/
  cash-runway producers, pension_calculator, the year_end_summary_service
  year-summary producers. Main session Reads each asserting line and
  classifies.

Produce in 07_test_gaps.md a "Part 7.A -- per-concept coverage census (slice
4)" section, one record per concept, every section-3.1 element, one verdict.
Confirm in the section that all 47 concepts now have a verdict. Do not run the
app or pytest. Do not modify code or tests. End by writing the section and
stopping; paste `git status`.
```

### Prompt P7-e (divergence-catching audit + symptom targets + cross-page meta-gap + report-only proposals)

```text
[anti-shortcut preamble]

This is Phase 7 session P7-e. Follow phase7_plan.md section 4 (P7-e), the
contract in section 2, and the schemas in sections 3.2 and 3.3. Scope: for
every DIVERGE finding in 03_consistency.md, the 5 symptom regression targets,
and the Phase 6 D6-/S6-/B6- equivalence-test implications, determine by reading
the suite whether a catching test exists; build Part 7.B and the report-only
Part 7.C. Do not open the prior test-audit docs.

Bounded reading:
- financial_calculation_audit_plan.md lines 696-704.
- 03_consistency.md: grep "Verdict:", enumerate the DIVERGE set, Read only
  those findings' divergence paragraphs; Read lines 6049-6075 (C2 + C3).
- 05_symptoms.md lines 1714-1721.
- 06_dry_solid.md lines 2193-2206.
- 07_test_gaps.md: the full Part 7.A already written (recovery state; read it,
  do not re-derive it).
- Live tests/ via Explore: per DIVERGE finding, the search for a test
  exercising both diverging paths or asserting the violated equality; for the
  cross-page meta-gap, the search for any fixture asserting one balance across
  grid + /savings + /accounts for the same period (expected empty -- paste it).

Produce in 07_test_gaps.md "Part 7.B" (every DIVERGE finding + 5 symptom
targets + the explicit cross-page meta-gap + the Phase 6 equivalence
implications, each with the catching-test grep result and section-3.2
elements) and "Part 7.C" (the PT-NN report-only proposed tests, each with the
hand-computed expected value and arithmetic, pinning the E-NN-correct value).
Do not run the app or pytest. Do not modify code or tests. End by writing the
sections and stopping; paste `git status`.
```

### Prompt P7-f (verification and consolidation gate)

```text
This session is part of a read-only audit running in Claude Code's `plan`
permission mode. Verify every factual claim by re-resolving the cited
file:line to live test source; do not recall from memory and do not trust a
prior session's citation without re-opening it. No new coverage analysis. The
gate goes into docs/audits/financial_calculations/07_test_gaps.md;
source/tests/migrations untouched and pytest is never invoked. Do not open the
prior test-audit docs.

This is Phase 7 session P7-f, the trust-but-verify capstone. Follow
phase7_plan.md section 4 (P7-f) and the acceptance gate in section 5. Read the
full docs/audits/financial_calculations/07_test_gaps.md, phase7_plan.md
sections 1-5, and re-grep 03_consistency.md for "Verdict:" (the DIVERGE set)
for the divergence-completeness reconciliation.

Do exactly these tasks, appending to 07_test_gaps.md:
1. Spot-check >= 15 cited claims at random across Part 7.A/7.B/7.C, mixing
   COVERED, NO-PINNED-TEST, and PINNED-AGAINST-DIVERGENT-BEHAVIOR verdicts;
   re-resolve each to live test source (grep/Read); show the table and pass
   count. Threshold 100%; any miss reopens that session before the gate passes
   -- record and stop.
2. Concept-completeness: all 47 controlled-vocabulary concepts have a Part 7.A
   verdict; no concept dropped; every PRIMARY PATH: UNKNOWN concept recorded
   PRODUCER-UNKNOWN-CANNOT-PIN.
3. Divergence-completeness: every DIVERGE finding has a Part 7.B entry; the 5
   symptom targets + the explicit cross-page meta-gap + the Phase 6
   equivalence implications all present.
4. Anti-coverage roll-up: one table of every
   PINNED-AGAINST-DIVERGENT-BEHAVIOR finding (the tests that break when the
   code is correctly fixed; CLAUDE.md rule 5 forbids "fixing" them); flag only,
   no test file was opened for modification.
5. Acceptance gate G1-G9 from phase7_plan.md section 5, each with
   evidence/verdict.
6. Handoff to Phase 8/9; carry the A-26 estimated_retirement_tax_rate tail
   forward unchanged.
7. Paste `git status` confirming only docs/audits/financial_calculations/
   changed; confirm no tests/ file was created/modified/deleted and pytest was
   never invoked.

End by recording "Phase 7 complete" with the G1-G9 roll-up, or, if any gate
fails, name the failing criterion and the session to reopen and stop without
declaring completion.
```
