> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Phase 5 Execution Plan -- Symptom-Driven Investigation

Meta-plan for executing Phase 5 of the financial-calculation audit. This file is
the launch script: each session below is started by pasting its section verbatim
plus the anti-shortcut prompt. This file is a planning artifact, not a phase
output and not source code. The phase output is `05_symptoms.md`.

Authoritative spec: `financial_calculation_audit_plan.md` section 5 (lines
564-612) and section 10 (how to run). This file does not override the audit
plan; it sequences it and binds the trust-but-verify contract the developer
requires.

## 0. Why Phase 5 is different now

Every open question Phases 3 and 4 stopped on (Q-08..Q-26) was answered by the
developer 2026-05-18 and recorded as behavioral expectations E-18..E-28 in
`00_priors.md` 0.3 (matching A-08..A-26 in `09_open_questions.md`). Phase 3's
own reconciliation states Phase 5 may consume `03_consistency.md` "with every
UNKNOWN-Q axis resolved."

Consequence: Phase 5 does not stop and ask. For every symptom branch the
intended behavior is fixed by a locked E-NN. Phase 5 roots each symptom against
that resolved intent and states a remediation direction. The only items Phase 5
may add to `09_open_questions.md` are genuinely new ambiguities discovered while
tracing -- not the already-answered Q-08..Q-26, and not auditor-invented
"obvious" expectations (audit-plan 0.3 prohibition still holds).

One tail item is explicitly out of symptom scope and must be carried, not
dropped: A-26 notes E-18 does not decide the `auth.user_settings.
estimated_retirement_tax_rate` NULL-semantics contract. It touches no balance or
loan symptom; P5-d records it as out-of-scope-carried so it is not silently
lost.

## 1. Inherited spine (hypotheses to falsify, not scaffolding)

Phase 3 C2 (`03_consistency.md:6049-6060`) and Phase 4 Deliverable 4
(`04_source_of_truth.md:2129-2146`) assert this symptom -> root-cause map. The
developer has directed **independent re-derivation**: Phase 5 builds each tree
from source first and only then compares to this table. A mismatch is a Phase 5
finding against the prior phase, surfaced, not smoothed.

| Symptom | Asserted root cause | Resolved intent |
| --- | --- | --- |
| #1 $160 grid vs $114.29 /savings, same current period | F-009 (+ W-277 calendar, same defect, new page) | E-25 / E-19 / E-27 |
| #2 $1911.54 / $1914.34 / $1912.94 -> $1910.95 | F-013 (16-site incompatible (P,r,n) triples) | E-18 |
| #3 current_principal not moving as transfers settle | F-014 (zero settle-writer; settle modules never import LoanParams) | E-18 |
| #4 5/5 ARM payment creep inside fixed window | F-026 (frozen stored principal re-amortized over shrinking remaining; arm_first_adjustment_months consumed by zero calc sites) | E-18 |
| #5 /accounts matches nowhere else | F-001 + F-008 (unlabeled per-page base divergence) | E-18 / E-19 |

Symptoms #2/#3/#4 are asserted to collapse onto one un-maintained column
(`current_principal`). Phase 5 must independently re-prove that collapse from
source or reject it.

## 2. The trust-but-verify contract (binds every Phase 5 session)

1. Every node in every hypothesis tree cites `file:line` Read at source during
   the Phase 5 session. Phase 3/4 line citations are re-opened and re-read, not
   quoted. Recall from memory is not evidence; `grep`/`glob`/full-file Read is.
2. Every worked example (the $160/$114.29 gap; the $1911.54 -> $1910.95
   sequence; the ARM month-over-month creep; the symptom-#5 per-page spread) is
   recomputed by hand from the arithmetic the code actually executes, with the
   intermediate Decimal steps shown. Copying a number from C2 is not
   recomputation.
3. The C2 map and the drift register are falsifiable hypotheses. If an
   independent backward trace lands on a different or additional root cause, the
   divergence from the prior phase is itself recorded in `05_symptoms.md`.
4. P5-d runs a Gate-B-style spot-check: >= 15 cited claims drawn at random from
   the three symptom slices, each re-resolved to the expected code; the table
   and pass count are shown.
5. Any tree branch where no Phase-3 finding applies is listed as
   re-investigation (audit-plan 5). This is how Phase 5 audits Phase 3's
   completeness; it is not a defect to hide.
6. Read-only. Plan permission mode. No app run, no code/test/migration/template/
   JS edit. Output is `05_symptoms.md` only; `git status` must show only
   `docs/audits/financial_calculations/` at every session end.

## 3. Per-symptom finding schema (audit-plan 5, with the contract additions)

Each symptom subsection in `05_symptoms.md` contains, in order:

- **Symptom** -- the developer's own words (from audit-plan 5 lines 592-611).
- **Reproduction path** -- page, the user input, the account/period that
  exhibits it.
- **Hypothesis tree** -- numbered (or indented) backward walk from the displayed
  value: each node = the value, the file:line that produced it (Read this
  session), its inputs, and the transformation. Not prose.
- **Per-branch finding linkage** -- each branch tagged with the Phase-3
  F-ID(s) that govern it, or `NO-FINDING -> RE-INVESTIGATE` with a one-line
  reason.
- **Worked example** -- concrete inputs, every Decimal step, the divergent
  outputs, hand-computed this session.
- **Best-evidence root cause** -- the combination of findings that most likely
  explains the symptom, stated as a cited hypothesis (not a verdict), against
  the resolved intent (the governing E-NN) with the remediation direction the
  E-NN already fixes.
- **Independent-vs-inherited note** -- whether the independent trace confirms,
  narrows, or contradicts the C2 / drift-register assertion for this symptom.
- **Verification plan** -- the queries, code reads, and hand computations that
  would confirm or reject the root cause. Documentation only; nothing is run.

## 4. Sessions

One symptom-family per session. `/clear` between sessions. Each session's
required reading is line-range-bounded to prevent the infinite-exploration /
context-blowout failure mode; read the live `app/` source in full for any
function before drawing a conclusion about it (CLAUDE.md rule 2; audit-plan hard
rule 3).

### P5-a -- Symptom #1 (checking balance: $160 grid vs $114.29 /savings)

Goal: backward tree from both displayed numbers to the divergent producer;
hand-derive the gap; fold in W-277 (calendar month-end, asserted same defect on
a new consuming page).

Required reading (bounded):
- `financial_calculation_audit_plan.md:564-612` (Phase 5 spec); `:592-611`
  (symptom #1 wording).
- `00_priors.md:198-213` (E-19), `:276-311` (E-25, E-27).
- `03_consistency.md:214-340` (F-002, F-003), `:649-726` (F-009),
  `:6049-6060` (C2), `:6162-6204` (Q-answer reconciliation).
- `04_source_of_truth.md:35-295` (Family A anchor), `:2129-2146` (drift row #1).
- `09_open_questions.md` Q-16/A-16 and Q-20/A-20 (anchor-None resolution).
- Live source, Read in full: the entries-`selectinload` site and balance call in
  `app/routes/grid.py`; `app/services/savings_dashboard_service.py` checking
  path; `app/services/balance_calculator.py` `_sum_remaining` / `_sum_all` /
  `_entry_aware_amount` / `effective_amount` branch; `app/services/
  calendar_service.py` month-end path (W-277).

Stop condition: symptom #1 subsection written to `05_symptoms.md` with the gap
hand-recomputed; session ends.

### P5-b -- Symptoms #2 + #3 + #4 (the one loan-resolver family)

Kept in one session deliberately: #2/#3/#4 are asserted to share a single root
column; splitting them fractures the shared proof. Independently re-prove (or
reject) the collapse.

Required reading (bounded):
- `financial_calculation_audit_plan.md:592-611` (symptoms #2/#3/#4 wording).
- `00_priors.md:184-197` (E-18).
- `03_consistency.md:1009-1149` (F-013), `:1150-1359` (F-014, F-015, F-016),
  `:1936-2039` (F-026), `:6049-6060` (C2 rows #2/#3/#4).
- `04_source_of_truth.md:296-743` (Family B principal incl. settle-update trace
  `:489-587` and the fixed-rate worked example `:588-655`), `:744-1320`
  (Family B rate/escrow incl. the ARM crux `:1028-1112` and the ARM worked
  example `:1113-1159`), `:2129-2146` (drift rows #2/#3/#4).
- `09_open_questions.md` Q-17/A-17, Q-22/A-22, Q-23/A-23 (the one
  stored-mirror-maintenance policy resolved by E-18).
- Live source, Read in full: `app/services/amortization_engine.py`
  (`get_loan_projection`, `calculate_monthly_payment`,
  `calculate_remaining_months`, the ARM branch); `app/routes/loan.py`
  (`dashboard`, `update_params`); `app/routes/debt_strategy.py`;
  `app/services/savings_dashboard_service.py` debt path. Grep-prove from source
  that no settle / transfer / recurrence / status-transition module imports
  `LoanParams` (the zero-settle-writer claim).

Stop condition: #2, #3, #4 subsections written, each with its own
hand-recomputed worked example (the triple divergence; the un-maintained-column
trace; the month-over-month ARM creep), plus an explicit statement of whether
the three collapse onto one column; session ends.

### P5-c -- Symptom #5 (/accounts matches nothing) + cross-symptom synthesis

Depends on P5-a (checking/anchor base) and P5-b (loan base). One concrete
`(user, period, scenario, account)` carried across every balance producer to
show the unlabeled per-page spread.

Required reading (bounded):
- `financial_calculation_audit_plan.md:592-611` (symptom #5 wording).
- `00_priors.md:184-213` (E-18, E-19).
- `03_consistency.md:109-213` (F-001), `:580-648` (F-008), `:6049-6060` (C2
  row #5).
- `04_source_of_truth.md:35-295` (Family A), `:296-743` (Family B principal),
  `:2129-2146` (drift row #5).
- The P5-a and P5-b subsections already written to `05_symptoms.md`.
- Live source, Read in full: the five balance producers named in F-001
  (`grid.py`, `accounts.py` checking/loan detail, `savings_dashboard_service.py`,
  `dashboard_service.py`, `year_end_summary_service.py`) at the
  account-balance sites.

Stop condition: symptom #5 subsection written with the single worked
`(user,period,scenario,account)` evaluated at every producer; session ends.

### P5-d -- Verification and consolidation gate (trust-but-verify capstone)

No new symptom analysis. Verify and consolidate.

Tasks:
1. Spot-check: >= 15 cited claims sampled at random across the P5-a/b/c trees,
   each re-resolved to source; show the table and the pass count (threshold:
   100%; any miss reopens that symptom before the gate can pass).
2. Completeness reconciliation: all 5 symptoms have reproduction path +
   hypothesis tree + per-branch linkage + hand-recomputed worked example +
   best-evidence root cause + verification plan; every tree node carries a
   this-session citation; every `NO-FINDING -> RE-INVESTIGATE` branch is
   enumerated.
3. Independent-vs-inherited roll-up: per symptom, confirm / narrow / contradict
   vs the C2 map and drift register; any contradiction is stated as a Phase 5
   finding against the prior phase with both citations.
4. Acceptance gate (section 5 below), each criterion with evidence/verdict.
5. Handoff: what Phase 6 (DRY/SOLID), Phase 7 (test gaps), Phase 8 (findings),
   Phase 9 (open questions) inherit. Record the A-26
   `estimated_retirement_tax_rate` NULL-semantics tail as out-of-symptom-scope
   carried (not dropped).
6. `git status` pasted, showing only `docs/audits/financial_calculations/`.

Stop condition: gate section appended to `05_symptoms.md`; "Phase 5 complete"
recorded with the gate roll-up; session ends.

## 5. Phase 5 acceptance gate (mirrors Phase 3 Gate / Phase 4 Deliverable 6)

Phase 5 is complete only when all hold, each with shown evidence:

- **G1** `05_symptoms.md` exists, non-empty; one subsection per symptom #1-#5,
  each carrying every schema element in section 3.
- **G2** Every hypothesis-tree node cites `file:line` Read during a Phase 5
  session (no node sourced only from Phase 3/4 prose).
- **G3** Every worked example is internally arithmetic-consistent and was
  hand-recomputed this phase (intermediate Decimal steps shown); the
  developer's reported figures ($160 / $114.29; $1911.54 / $1914.34 /
  $1912.94 / $1910.95; the ARM creep) are reproduced or the discrepancy is
  explained.
- **G4** Every tree branch maps to a Phase-3 F-ID or is listed under
  re-investigation with a reason.
- **G5** Spot-check >= 15 claims, 100% resolve; table and count shown.
- **G6** Each symptom's best-evidence root cause is stated against its
  governing E-NN with the remediation direction that E-NN already fixes; no
  symptom verdict is left blocked (all Q-08..Q-26 are answered).
- **G7** Independent-vs-inherited note present for all 5 symptoms; any
  contradiction of C2 / drift register surfaced as a finding.
- **G8** No new auditor-invented "obvious" expectation added to
  `09_open_questions.md`; only genuinely new ambiguities, if any.
- **G9** `git status` shows only `docs/audits/financial_calculations/` files
  changed; no source, test, migration, template, or JS file touched.

## 6. Anti-shortcut prompt (paste at the top of every Phase 5 session)

> This session is part of a read-only audit running in Claude Code's `plan`
> permission mode. Document findings in `05_symptoms.md` with file and line
> citations to the actual source, Read this session. Read the relevant function
> fully before drawing conclusions about its behavior. Verify every factual
> claim by running `grep`, `glob`, or a full-file Read; do not recall from
> memory and do not quote Phase 3/4 citations without re-opening them. Treat the
> Phase 3 C2 map and Phase 4 drift register as hypotheses to falsify, not
> scaffolding: build the hypothesis tree from source first, then compare. Every
> worked example is recomputed by hand with intermediate Decimal steps shown.
> Every Q-08..Q-26 is answered (E-18..E-28); root each symptom against its
> governing E-NN and state the remediation direction -- do not defer. Findings
> go into `05_symptoms.md`; source files, tests, and migrations remain
> untouched. Stay within the assigned session's symptom scope.

## 7. Failure modes and remedies

- Kitchen-sink session -> one symptom-family per session; `/clear` between.
- Infinite exploration -> the bounded reading lists in section 4; do not widen
  without recording why.
- Trust-then-verify gap -> section 2 contract; the P5-d spot-check is mandatory
  and Phase 3/4 are re-opened, not quoted.
- Mis-rooting -> independent re-derivation can overturn the C2 map; that is a
  finding (G7), not an error to conceal.
- Scope drift -> if a session starts rewriting non-financial code or proposing
  fixes, stop; the deliverable is the explanation, not a patch (audit-plan
  10.6/10.7).

## 8. Ready-to-paste session prompts

These prompts are static and do **not** depend on the *content* of any prior
session's output. Each session's recovery state is the accumulated
`05_symptoms.md` file (audit-plan 10.5): later prompts instruct the agent to
*read the file that now exists*, but the prompt text itself never changes based
on what a prior session concluded. Run them strictly in order
(P5-a -> P5-b -> P5-c -> P5-d), each in its own session started with
`claude --permission-mode plan`, with `/clear` between. Do not use `@` on the
large audit docs (`00`/`01`/`03`/`04`/`09`) -- `@` reads the whole file and
blows context, the exact failure mode section 4 bounds against; the prompts
instruct ranged Reads instead.

### Prompt P5-a (symptom #1)

```text
This session is part of a read-only audit running in Claude Code's `plan`
permission mode. Document findings in
docs/audits/financial_calculations/05_symptoms.md with file and line citations
to the actual source, Read this session. Read the relevant function fully
before drawing conclusions about its behavior. Verify every factual claim by
running grep, glob, or a full-file Read; do not recall from memory and do not
quote Phase 3/4 citations without re-opening them. Treat the Phase 3 C2 map and
Phase 4 drift register as hypotheses to falsify, not scaffolding: build the
hypothesis tree from source first, then compare. Every worked example is
recomputed by hand with intermediate Decimal steps shown. Every Q-08..Q-26 is
answered (E-18..E-28); root the symptom against its governing E-NN and state
the remediation direction -- do not defer. Findings go into 05_symptoms.md;
source files, tests, and migrations remain untouched. Stay within symptom #1.

This is Phase 5 session P5-a. Follow phase5_plan.md section 4 (P5-a) and the
trust-but-verify contract in section 2. Scope: developer symptom #1 only --
projected end balance shows ~$160 on the grid for the current pay period but
~$114.29 as the checking balance on /savings; both must be the same number
from the same inputs.

Bounded reading (Read exactly these ranges, in full, before concluding;
do not widen without recording why):
- docs/audits/financial_calculations/financial_calculation_audit_plan.md
  lines 564-612.
- docs/audits/financial_calculations/00_priors.md lines 198-213 (E-19) and
  276-311 (E-25, E-27).
- docs/audits/financial_calculations/03_consistency.md lines 214-340 (F-002,
  F-003), 649-726 (F-009), 6049-6060 (C2), 6162-6204 (Q-answer
  reconciliation).
- docs/audits/financial_calculations/04_source_of_truth.md lines 35-295
  (Family A) and 2129-2146 (drift row #1).
- docs/audits/financial_calculations/09_open_questions.md: the Q-16/A-16 and
  Q-20/A-20 entries (grep for "Q-16 (" / "A-16 (" / "Q-20 (" / "A-20 (").
- Live app/ source, Read in full at the relevant functions: app/routes/grid.py
  (the entries selectinload site and the balance-calculator call),
  app/services/savings_dashboard_service.py (the checking-balance path),
  app/services/balance_calculator.py (_sum_remaining, _sum_all,
  _entry_aware_amount, the effective_amount branch),
  app/services/calendar_service.py (the month-end path, for W-277).

Produce in 05_symptoms.md a "Symptom #1" subsection with every element of
phase5_plan.md section 3: symptom in the developer's words, reproduction path,
a numbered backward hypothesis tree from both displayed numbers ($160 grid,
$114.29 /savings) to the divergent producer, per-branch F-ID linkage (or
NO-FINDING -> RE-INVESTIGATE), a hand-recomputed worked example deriving the
gap with intermediate Decimal steps, best-evidence root cause against E-25/
E-19/E-27 with the remediation direction, an independent-vs-inherited note vs
C2/drift row #1, and a verification plan. Include W-277 (calendar month-end as
the same defect on a new consuming page). Do not run the app. Do not modify
code. End the session by writing the subsection and stopping; paste
`git status` to confirm only docs/audits/financial_calculations/ changed.
```

### Prompt P5-b (symptoms #2 + #3 + #4)

```text
This session is part of a read-only audit running in Claude Code's `plan`
permission mode. Document findings in
docs/audits/financial_calculations/05_symptoms.md with file and line citations
to the actual source, Read this session. Read the relevant function fully
before drawing conclusions about its behavior. Verify every factual claim by
running grep, glob, or a full-file Read; do not recall from memory and do not
quote Phase 3/4 citations without re-opening them. Treat the Phase 3 C2 map and
Phase 4 drift register as hypotheses to falsify, not scaffolding: build the
hypothesis tree from source first, then compare. Every worked example is
recomputed by hand with intermediate Decimal steps shown. Every Q-08..Q-26 is
answered (E-18..E-28); root each symptom against its governing E-NN and state
the remediation direction -- do not defer. Findings go into 05_symptoms.md;
source files, tests, and migrations remain untouched. Stay within symptoms
#2/#3/#4.

This is Phase 5 session P5-b. Follow phase5_plan.md section 4 (P5-b) and the
trust-but-verify contract in section 2. Scope: developer symptoms #2, #3, #4
only. #2: mortgage monthly payment observed at $1911.54 / $1914.34 / $1912.94
on different views, then $1910.95 after editing current principal on
/accounts/3/loan. #3: current principal does not update as transfers to the
mortgage settle. #4: a 5/5 ARM monthly payment creeps a few dollars
month-over-month inside its fixed-rate window. These three are asserted to
collapse onto one un-maintained column (current_principal); independently
re-prove or reject that collapse from source.

Bounded reading (Read exactly these ranges, in full, before concluding;
do not widen without recording why):
- docs/audits/financial_calculations/financial_calculation_audit_plan.md
  lines 592-611.
- docs/audits/financial_calculations/00_priors.md lines 184-197 (E-18).
- docs/audits/financial_calculations/03_consistency.md lines 1009-1149
  (F-013), 1150-1359 (F-014, F-015, F-016), 1936-2039 (F-026), 6049-6060
  (C2 rows #2/#3/#4).
- docs/audits/financial_calculations/04_source_of_truth.md lines 296-743
  (Family B principal incl. settle-update trace 489-587 and the fixed-rate
  worked example 588-655), 744-1320 (Family B rate/escrow incl. the ARM crux
  1028-1112 and the ARM worked example 1113-1159), 2129-2146 (drift rows
  #2/#3/#4).
- docs/audits/financial_calculations/09_open_questions.md: the Q-17/A-17,
  Q-22/A-22, Q-23/A-23 entries (grep for those markers).
- Live app/ source, Read in full at the relevant functions:
  app/services/amortization_engine.py (get_loan_projection,
  calculate_monthly_payment, calculate_remaining_months, the ARM branch),
  app/routes/loan.py (dashboard, update_params), app/routes/debt_strategy.py,
  app/services/savings_dashboard_service.py (the debt path). Grep-prove from
  source that no settle / transfer / recurrence / status-transition module
  imports LoanParams (the zero-settle-writer claim).

Produce in 05_symptoms.md three subsections, "Symptom #2", "Symptom #3",
"Symptom #4", each with every element of phase5_plan.md section 3, and an
explicit statement of whether the three collapse onto one column. Hand-recompute
each worked example with intermediate Decimal steps: the #2 triple divergence
(reproduce or explain $1911.54/$1914.34/$1912.94 and the $1910.95-after-edit
value), the #3 un-maintained-column trace, the #4 month-over-month ARM creep.
Root each against E-18 with the remediation direction. Do not run the app.
Do not modify code. End the session by writing the three subsections and
stopping; paste `git status` to confirm only
docs/audits/financial_calculations/ changed.
```

### Prompt P5-c (symptom #5 + cross-symptom synthesis)

```text
This session is part of a read-only audit running in Claude Code's `plan`
permission mode. Document findings in
docs/audits/financial_calculations/05_symptoms.md with file and line citations
to the actual source, Read this session. Read the relevant function fully
before drawing conclusions about its behavior. Verify every factual claim by
running grep, glob, or a full-file Read; do not recall from memory and do not
quote Phase 3/4 citations without re-opening them. Treat the Phase 3 C2 map and
Phase 4 drift register as hypotheses to falsify, not scaffolding: build the
hypothesis tree from source first, then compare. Every worked example is
recomputed by hand with intermediate Decimal steps shown. Every Q-08..Q-26 is
answered (E-18..E-28); root the symptom against its governing E-NN and state
the remediation direction -- do not defer. Findings go into 05_symptoms.md;
source files, tests, and migrations remain untouched. Stay within symptom #5.

This is Phase 5 session P5-c. Follow phase5_plan.md section 4 (P5-c) and the
trust-but-verify contract in section 2. Scope: developer symptom #5 only --
account balances on /accounts do not match the balances shown anywhere else in
the app -- plus the cross-symptom synthesis it depends on.

Bounded reading (Read exactly these ranges, in full, before concluding;
do not widen without recording why):
- docs/audits/financial_calculations/financial_calculation_audit_plan.md
  lines 592-611.
- docs/audits/financial_calculations/00_priors.md lines 184-213 (E-18, E-19).
- docs/audits/financial_calculations/03_consistency.md lines 109-213 (F-001),
  580-648 (F-008), 6049-6060 (C2 row #5).
- docs/audits/financial_calculations/04_source_of_truth.md lines 35-295
  (Family A), 296-743 (Family B principal), 2129-2146 (drift row #5).
- docs/audits/financial_calculations/05_symptoms.md: the already-written
  Symptom #1 and Symptom #2/#3/#4 subsections (recovery state; read them, do
  not re-derive them).
- Live app/ source, Read in full at the account-balance sites named in F-001:
  app/routes/grid.py, app/routes/accounts.py (checking detail and loan
  detail), app/services/savings_dashboard_service.py,
  app/services/dashboard_service.py, app/services/year_end_summary_service.py.

Produce in 05_symptoms.md a "Symptom #5" subsection with every element of
phase5_plan.md section 3, carrying ONE concrete (user, period, scenario,
account) tuple evaluated by hand at every one of the five balance producers to
show the unlabeled per-page spread, rooted against E-18/E-19 with the
remediation direction, with the independent-vs-inherited note vs C2/drift
row #5. Do not run the app. Do not modify code. End the session by writing the
subsection and stopping; paste `git status` to confirm only
docs/audits/financial_calculations/ changed.
```

### Prompt P5-d (verification and consolidation gate)

```text
This session is part of a read-only audit running in Claude Code's `plan`
permission mode. Verify every factual claim by re-resolving the cited
file:line to source; do not recall from memory and do not trust a prior
session's citation without re-opening it. No new symptom analysis. Findings
and the gate go into docs/audits/financial_calculations/05_symptoms.md; source
files, tests, and migrations remain untouched.

This is Phase 5 session P5-d, the trust-but-verify capstone. Follow
phase5_plan.md section 4 (P5-d) and the acceptance gate in section 5. Read the
full docs/audits/financial_calculations/05_symptoms.md (all of Symptom
#1..#5), phase5_plan.md sections 2-5, and
docs/audits/financial_calculations/03_consistency.md lines 6049-6060 (C2) and
04_source_of_truth.md lines 2129-2146 (drift register) for the
independent-vs-inherited roll-up.

Do exactly these tasks, appending to 05_symptoms.md:
1. Spot-check: choose >= 15 cited claims at random across the Symptom #1..#5
   trees; re-resolve each to source (grep/Read); show the table and the pass
   count. Threshold is 100%; any miss reopens that symptom before the gate can
   pass -- record that and stop.
2. Completeness reconciliation: confirm all 5 symptoms carry every section-3
   schema element, every tree node has a this-session citation, and every
   NO-FINDING -> RE-INVESTIGATE branch is enumerated.
3. Independent-vs-inherited roll-up: per symptom, confirm/narrow/contradict vs
   the C2 map and drift register; any contradiction is a Phase 5 finding
   against the prior phase, with both citations.
4. Acceptance gate: criteria G1-G9 from phase5_plan.md section 5, each with
   evidence/verdict.
5. Handoff: what Phase 6/7/8/9 inherit; record the A-26
   estimated_retirement_tax_rate NULL-semantics tail as out-of-symptom-scope
   carried (not dropped).
6. Paste `git status`, confirming only docs/audits/financial_calculations/
   changed.

End by recording "Phase 5 complete" with the G1-G9 roll-up, or, if any gate
fails, name the failing criterion and the symptom to reopen and stop without
declaring completion.
```
