> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Phase 8 Execution Plan -- Findings Report

Meta-plan for executing Phase 8 of the financial-calculation audit. This file is
the launch script: each session below is started by pasting its ready-to-paste
prompt (section 8) verbatim. This file is a planning artifact, not a phase
output and not source code. The phase output is `08_findings.md`.

Authoritative spec: `financial_calculation_audit_plan.md` section 8 (lines
705-743) and section 10 (how to run). This file does not override the audit
plan; it sequences it and binds the trust-but-verify contract the developer
requires. Where the audit plan and this file appear to differ, the audit plan
wins and the divergence is a defect in this file to be reported, not worked
around -- with the two explicit, pre-identified audit-plan defects in section 0
as the only standing exceptions (an instruction that is internally impossible is
reported and resolved, not obeyed into a collision).

## 0. Why Phase 8 is different now

Phase 8 is the capstone. Phases 1-7 asked "what is wrong, where, why, and is it
tested." Phase 8 asks the only question the developer acts on: **of everything
proven, what must be fixed first, and can the developer trust this list enough
to bet real money on the order.** This is the developer-facing deliverable the
entire audit exists to produce. A dropped finding or a mis-graded severity here
is the trust-then-verify trap (audit-plan 10.8) at the single worst place it can
occur -- the one document the developer reads instead of the other eight.

Five things make Phase 8 different from a generic summary:

1. **Severity is ASSIGNED here, for the first time, and it is the load-bearing
   new judgment.** Every prior phase explicitly deferred it: Phase 3 "Severity
   is a Phase-8 assignment ... pre-flagged from the Read-confirmed money
   impact"; Phase 5 "the C3 CRITICAL pre-list is Phase 8's severity input;
   Phase 5 adds no severity"; Phase 6 "severity assigned in Phase 8, not here";
   Phase 7 "severity is assigned there, not here." Phase 8 does not inherit a
   severity. It derives each one from the audit-plan rubric (section 5) against
   a cited money impact. The C3 CRITICAL pre-list (`03_consistency.md:6062+`)
   is an **input -- the candidate set**, not the authority: every candidate is
   re-tested against the rubric, and every non-candidate finding is checked for
   a missed CRITICAL. The audit plan says the reported symptoms are "likely
   CRITICAL"; *likely* is a hypothesis to verify per finding, not a label to
   apply.

2. **Phase 8 aggregates; it does not re-audit.** Whether a number is wrong was
   settled by Phase 3/5; what the correct behavior is was settled by the locked
   E-01..E-28. Phase 8 clusters proven findings by governing root cause,
   assigns severity, and writes the plain-language summary. Re-opening a settled
   verdict, re-deriving a worked example from scratch, or re-litigating an
   answered question is scope drift, not diligence. The diligence Phase 8 owes
   is the opposite direction: re-resolving each cited `file:line` to **live
   source** so the developer-facing summary does not ship a stale pointer
   (section 2 contract item 1).

3. **Clustering is by governing E-NN root cause (developer-directed).** The
   developer selected root-cause clustering 2026-05-18; Phase 5's handoff is
   explicit: "Phase 8 should treat [#2/#3/#4] as one finding family with one
   remediation (E-18 single resolver), not three independent fixes." The same
   root recurs across phases (E-18 alone subsumes F-013/F-014/F-015/F-016/F-026
   + symptoms #2/#3/#4 + D6-01 + the Phase-4 `current_principal`/`interest_rate`
   UNCLEAR pair + Q-22/Q-23 + PA-27/PA-28). The cluster map is a **falsifiable
   surjection hypothesis**, not a convenience: the gate proves every source ID
   maps to exactly one Phase 8 finding with zero orphans and zero double-maps.

4. **Phase 8 integrates the prior-audit backlog.** `00_priors.md` 0.6 line 806
   is a standing instruction: "Phase 8 integrates these into the new findings
   document." All thirty PA-01..PA-30 are integrated -- each is subsumed into a
   cluster with the citation, or carried as its own finding if no later phase
   covered it. None is silently dropped; the reverse-index proves it.

5. **Three pre-identified defects must be reported, not obeyed or smoothed:**
   - The audit-plan Phase-8 instruction "ID (F-001, F-002, ...)" collides
     catastrophically with Phase 3's live F-001..F-056. Obeying it literally is
     internally impossible. **Resolution (developer-approved 2026-05-18):**
     Phase 8 findings use the severity-prefixed scheme `CRIT-NN` / `HIGH-NN` /
     `MED-NN` / `LOW-NN`, with a master traceability matrix back to every
     source ID. This is recorded in `08_findings.md` as an
     audit-plan-vs-execution divergence under the standing rule.
   - The audit-plan output path `docs/audit/financial_calculations/08_findings.md`
     is a typo: the real, established path every prior phase wrote to is
     `docs/audits/financial_calculations/08_findings.md` (plural `audits`).
     Use the real path; record the typo as a one-line note, do not create the
     wrong directory.
   - Cross-phase count/citation discrepancies already exist and Phase 8 is
     where they surface: Phase 3 C1 records 21 DIVERGE while Phase 7 Part 7.B
     enumerates 20; Phase 4 found a Phase-3 `F-044` miscite (recorded, not
     fixed, per audit protocol); Phase 6 found the carried "470-line
     `savings.py:dashboard` SRP" tag stale (now 4 lines, real violation moved
     to `investment.py`). Phase 8 reconciles each in the open; an
     unreconcilable one is a finding against the prior phase with both
     citations (section 2 contract item 7), never quietly averaged away.

One tail is carried unchanged, not resolved here: Q-26 sub-question 2, the
`auth.user_settings.estimated_retirement_tax_rate` NULL-semantics contract
(model comment promises a bracket-based fallback; code applies no tax). Phase 8
records the documentation-correction finding but does not decide the contract;
it is carried to Phase 9 unchanged, exactly as Phases 5/6/7 carried it (G8).

## 1. Inherited spine (the source-ID universe Phase 8 must fully consume)

Phase 8 inherits one concrete obligation: **every source finding ID below maps
to exactly one Phase 8 finding, or is explicitly recorded "NOT-A-FINDING" with
a reason.** This table is the left side of the surjection. The counts are the
register as the prior phases left it; a count that does not reconcile at the
source register this phase is itself a Phase 8 finding against the prior phase
(section 0 item 5, contract item 7). This is the **starting set, not the
closing set**: a source finding discovered in a phase doc but absent from a
register is still consumed; a genuinely new ambiguity goes to Phase 9, never as
a Phase 8 finding.

| Source register | Where | Count (as left by prior phase) | Phase 8 obligation |
| --- | --- | --- | --- |
| Phase 3 consistency findings | `03_consistency.md` F-001..F-056 | 56 (24 AGREE, 21 DIVERGE, 9 UNKNOWN, 1 DEAD_CODE F-040, 1 MIXED F-056) | Every DIVERGE/UNKNOWN/MIXED -> a finding-cluster; every AGREE -> NOT-A-FINDING (AGREE); F-040 -> LOW dead-code |
| Phase 3 C2 symptom map | `03_consistency.md:6049-6060` | 5 symptoms -> F-009, F-013, F-014, F-026, F-001+F-008 | The CRITICAL spine; Phase 8's symptom->finding map is reconciled against this (G6) |
| Phase 3 C3 CRITICAL pre-list | `03_consistency.md:6062-6075` | F-037, F-042, Q-19/W-262, W-065/W-277, + 6 symptom findings | Severity *input* only; each re-tested against the rubric |
| Phase 3 W-NN non-HOLDS corpus | `03_consistency.md` cmp-1..5 | W-019 VIOLATED; W-010/013/014/016/018/021/022/030/040 + W-126 PARTIALLY_HOLDS (PLAN_DRIFT) | Each -> a finding or NOT-A-FINDING (structural-no-wrong-number / superseded) |
| Phase 4 stored-column findings | `04_source_of_truth.md` Families A-D + `F-046-SoT` | ~52 columns (1 CACHED, 6-7 UNCLEAR, rest AUTHORITATIVE) | Every UNCLEAR/CACHED-with-risk/GAP -> a finding with blocking Q; AUTHORITATIVE -> NOT-A-FINDING; the `F-044` miscite carried corrected |
| Phase 4 drift register | `04_source_of_truth.md:2129-2146` | symptom -> column -> F-ID -> blocking-Q for #1-#5 | Reconciled against Phase 3 C2 and Phase 5 (G6) |
| Phase 5 symptom roots | `05_symptoms.md` #1-#5 + collapse note | 5 symptoms; #2/#3/#4 collapse onto one column | The CRITICAL spine; #2/#3/#4 are ONE cluster (developer-directed) |
| Phase 6 structural findings | `06_dry_solid.md` D6-01..D6-10, S6-01..S6-07, B6-01..B6-03 | 20 | Each -> a finding or folded into the E-NN cluster it cross-links; B6-01/B6-02 are negative findings (HOLDS) recorded as such |
| Phase 6 carried standards finding | `06_dry_solid.md` handoff | the `loan/_escrow_list.html:37` E-16 site | A standards finding (or folded into an E-16/E-17 standards cluster) |
| Phase 6 findings-against-prior | `06_dry_solid.md` handoff | stale 470-line tag; W- label-vs-source set; D6-01/02/05 carry-undercounts | Carried as the corrected state, not the stale tag (G6) |
| Phase 7 non-COVERED concepts | `07_test_gaps.md` Part 7.A | 19 (5 NO-PINNED-TEST, 4 LOOSE-ONLY, 8 BLOCKED-ON-OPEN-QUESTION, 2 PRODUCER-UNKNOWN) | Each -> a test-gap finding or folded into the cluster whose number it fails to lock |
| Phase 7 divergence-catching gap | `07_test_gaps.md` Part 7.B | 20 DIVERGE with NO catching test + the cross-page meta-gap | The meta-gap is its own HIGH finding; per-finding gaps fold into their cluster |
| Phase 7 anti-coverage flags | `07_test_gaps.md` Part 7.F.4 | 3 conditional (`debt_total` Q-15; `goal_progress` GP2 Q-08; `federal_tax` F-040) | Each recorded so a green bar is not laundered as coverage |
| Phase 7 proposed tests | `07_test_gaps.md` Part 7.C | PT-01..PT-20 (PT-09 omitted) | Cross-referenced from the cluster each would lock; not findings themselves |
| Prior-audit backlog | `00_priors.md` 0.6 | PA-01..PA-30 | ALL integrated (subsumed-with-citation or carried); none dropped (line 806) |
| Phase 1 standards flags | `01_inventory.md` | TA-01..TA-11 Jinja arithmetic; 3+3 JS recompute sites | Folded into the E-16/E-17 standards cluster(s) |
| Resolved intent | `00_priors.md` 0.3 E-01..E-28; `09_open_questions.md` Q-01..Q-25 ANSWERED | 28 expectations; Q-08..Q-25 answered | Consumed as the governing end state per cluster; NOT re-litigated |
| Carried tail | `09_open_questions.md` Q-26 sub-2 | 1 | Documentation-correction finding recorded; contract carried to Phase 9 unchanged |

## 2. The trust-but-verify contract (binds every Phase 8 session)

1. **Every Phase 8 finding's evidence citation is re-resolved to LIVE source
   this session.** "Evidence: `amortization_engine.py:950-978`" requires the
   `grep`/Read against the current file this session with the key line quoted,
   not the citation copied from `03_consistency.md`. Phase 8 is the document the
   developer reads; a stale pointer here is the worst-placed stale pointer in
   the audit. A citation that no longer resolves (line moved, function renamed)
   is recorded as a finding against the prior phase with the old and the current
   location, and the current location is used.
2. **Severity is derived from the rubric against a cited money impact, never
   copied from C3.** Every `CRIT-NN` names the specific displayed figure, the
   page and `file:line` where it renders, and the hand-figure -- or the
   Phase-3/Phase-5 worked example it cites, re-read this session. A CRITICAL
   with no cited displayed wrong-dollar (or, for the data-loss class, no cited
   irreversible-destruction path) is downgraded or the session is reopened.
   This is the load-bearing Phase 8 rule, exactly as
   `PINNED-AGAINST-DIVERGENT-BEHAVIOR` was Phase 7's.
3. **The cluster map is a surjection to prove, not assert.** P8-a builds the
   master reverse-index (schema 3.2): every section-1 source ID -> exactly one
   finding, or `NOT-A-FINDING: <reason>`. Zero source ID absent; zero source ID
   mapped twice. The P8-e gate re-runs this mechanically (G4).
4. **No new finding.** Phase 8 records only what Phases 1-7 proved. A finding
   the auditor concludes is "obviously also true" but that no prior phase
   established is exactly the silent-drift trap the audit hunts; it is not
   added. A genuinely new ambiguity goes to `09_open_questions.md` with where it
   arose, never into `08_findings.md` (mirrors every prior phase's G8).
5. **UNKNOWN stays UNKNOWN.** A Phase-3 UNKNOWN, a Phase-4 UNCLEAR, a Phase-7
   `BLOCKED-ON-OPEN-QUESTION` is recorded with its blocking Q cited and a
   severity reflecting drift risk under realistic inputs; Phase 8 does not pick
   the interpretation that "looks right." Resolving an open question is scope
   drift.
6. **No fix.** Exactly one "remediation direction" sentence per finding,
   consistent with the governing E-NN (which already states the end state). No
   diff, no step list, no migration sketch, no file to write. A diff produced
   in-session is a finding artifact to revert and record as the
   remediation-direction sentence (audit-plan 10.6).
7. **Cross-phase discrepancies are surfaced, not smoothed.** The 21-vs-20
   DIVERGE count, the `F-044` miscite, the stale 470-line tag, and any citation
   that fails re-resolution are each reconciled in the open; one that cannot be
   reconciled is a finding against the prior phase with both citations. Phase 8
   never averages a discrepancy into a single number to make the report tidy.
8. **Read-only. Plan permission mode.** No app run, no code/test/migration/
   template/JS edit. Output is `08_findings.md` only; `git status` shows only
   `docs/audits/financial_calculations/` at every session end. The main session
   runs Phase 8 directly (audit-plan 10.1c: synthesis phase, Explore not used)
   but reading is register/handoff-bounded per section 4 to prevent the
   context blowout the 29k-line phase corpus would otherwise cause: a session
   reads registers + handoffs + C2/C3 + drift register in bounded ranges, and
   the deep finding body only for the clusters that session writes, bounded to
   the constituent source IDs' line ranges. The large audit docs are never read
   whole and never referenced with `@`.

## 3. Schemas

`08_findings.md` has three parts: Part 8.A (the per-finding records, sorted by
severity then symptom), Part 8.B (the master reverse-index / surjection proof),
Part 8.C (the verification gate, appended by P8-e).

### 3.1 Per-finding record (Part 8.A), one per root-cause cluster

- **ID** -- `CRIT-NN` / `HIGH-NN` / `MED-NN` / `LOW-NN`, numbered in severity
  order; within `CRIT`, the developer's reported symptoms first, then the other
  CRITICALs (audit-plan 8 sort rule, encoded in the ID).
- **Severity + rubric justification** -- the tier and the one-line cited reason
  it is that tier (the money figure / irreversibility / realistic blast
  radius), against section 5. For `CRIT`: the displayed figure, the page +
  `file:line` where it renders, and the hand-figure or the cited Phase-3/5
  worked example re-read this session (contract item 2).
- **Category** -- the audit-plan set, multi-valued allowed: `drift` |
  `source-of-truth` | `DRY` | `SOLID` | `test gap` | `definition`.
- **Plain-language description** -- one paragraph; the developer must
  understand the issue from this paragraph alone, with no phase-doc
  archaeology and no internal jargon (no bare "F-013", spell out the effect).
- **Subsumes (traceability block)** -- every constituent source ID with its
  prior-phase verdict: Phase-3 `F-NN` (+ verdict + classification), Phase-4
  column(s) (+ class), symptom `#N`, Phase-6 `D6/S6/B6-NN`, Phase-7 concept
  verdict / `PT-NN`, `PA-NN`, `W-NN`, `TA-NN`, the governing `E-NN`, the
  answered `Q-NN/A-NN`. This block is the exact reverse of the master index
  entry (schema 3.2) and the two are cross-checked in G4.
- **Governing E-NN** -- the locked expectation defining the correct end state,
  or `NONE -> structural-only` / `NONE -> standards-only`.
- **Evidence** -- `file:line` re-resolved to live source this session
  (contract item 1), the key line quoted.
- **Phase-doc pointers** -- where the full analysis lives (e.g.
  `03_consistency.md` F-013/F-014..., `05_symptoms.md` #2-#4, `06_dry_solid.md`
  D6-01).
- **Open questions** -- the blocking `Q-NN` if the cluster contains an
  UNKNOWN/UNCLEAR/BLOCKED member, else `none`.
- **Remediation direction** -- exactly one sentence, consistent with the
  governing E-NN (contract item 6).
- **Blast radius / symptom link** -- which displayed figure ships wrong on
  which page; the developer-reported symptom it explains, or `no observed
  symptom (latent)`.

### 3.2 Master reverse-index (Part 8.B) -- the surjection proof

One row per source ID in the section-1 universe, sorted by source register:

`source ID | prior-phase verdict | -> Phase 8 finding ID | OR NOT-A-FINDING: <reason>`

`NOT-A-FINDING` reasons are a closed set: `AGREE` (Phase-3 verdict) | `COVERED`
(Phase-7 verdict) | `AUTHORITATIVE` (Phase-4 class, no drift surface) |
`HOLDS` (Phase-6 negative finding / W-NN) | `superseded-by-A-NN` |
`resolved-intent-not-a-defect` (an answered Q consumed as the end state).
G4 requires: zero section-1 ID absent from this table; zero ID with two
`-> finding` mappings; every `PA-01..PA-30` present; every Phase-3 DIVERGE and
every Phase-4 UNCLEAR present with a `-> finding` (never `NOT-A-FINDING`).

### 3.3 Severity rubric (audit-plan section 8, with the two refinements this
evidence forces)

- **CRITICAL** -- a wrong dollar amount on a page the developer relies on for
  budgeting decisions, the divergence not visible to the user as an error.
  - *Refinement A (data-loss):* irreversible silent destruction of settled
    financial history is CRITICAL -- it mismanages real money (CLAUDE.md), and
    the harm is the loss, not a wrong displayed number. This is the
    Q-19/W-262 RECEIVED-template hard-delete class.
  - *Refinement B (symptoms):* the five developer-reported symptoms are
    CRITICAL by the audit plan's own statement, **verified here** against the
    rubric (each must have a cited displayed wrong dollar this session), not
    assumed from the audit plan's "likely."
- **HIGH** -- structural duplication or stored/computed drift not yet observed
  wrong but sufficient to produce a wrong dollar under realistic inputs;
  includes the **absence of any regression lock for a proven CRITICAL** (the
  Phase-7 cross-page meta-gap: the developer's two worst symptoms cannot be
  pinned) and the Phase-4 UNCLEAR stored columns (drift surface, blocked on a
  developer decision).
- **MEDIUM** -- DRY/SOLID structure with no current wrong number, missing
  tests for important invariants, definition ambiguity in non-customer-facing
  places, standards violations (Jinja/JS arithmetic) numerically consistent
  today.
- **LOW** -- formatting, naming, minor duplication with low blast radius,
  coincidental agreement, dead code carrying an inert divergence (F-040), the
  minor Phase-4 classification nit.

Severity is the cluster's maximum over its members justified by the rubric, not
an average; a cluster containing one CRITICAL member is CRITICAL even if it also
subsumes MEDIUM structural members (the structure is the substrate, the wrong
dollar is the harm).

## 4. Sessions

One concern-tier per session. `/clear` between sessions. Required reading is
line-range-bounded to prevent the infinite-exploration / context-blowout
failure mode the 29k-line phase corpus would otherwise cause. The large audit
docs (`01`..`07`, `09`) are **never** read whole and **never** referenced with
`@`; sessions grep them for the named anchors and Read only the bounded ranges.
Each session's recovery state is the accumulated `08_findings.md` (audit-plan
10.5): later sessions read what exists, they do not re-derive it.

### P8-a -- Build the cluster map and the master reverse-index (the surjection skeleton)

Goal: define the root-cause clusters and prove, mechanically, that every
section-1 source ID maps to exactly one cluster or to `NOT-A-FINDING`. No
severity, no prose paragraphs, no remediation sentences -- the spine only.
Highest-stakes session: a wrong cluster boundary or a dropped source ID here
poisons every later session.

Required reading (bounded; grep for the anchor, Read only the named range):
- `financial_calculation_audit_plan.md:705-743` (Phase 8 spec) and `:1005-1032`
  (acceptance criteria).
- `00_priors.md:154-329` (E-01..E-28 -- the cluster keys) and `:804-841`
  (PA-01..PA-30 -- the integration obligation).
- `03_consistency.md`: grep `^### Finding F-`, `^## Finding F-`, `Verdict:`;
  Read only the `C1` consolidated register and `:6049-6075` (C2 + C3). Do not
  read the finding bodies this session -- the verdict line and concept token
  per F-NN are sufficient for the map.
- `04_source_of_truth.md:2090-2270` (the consolidated classification table +
  drift register + the `F-044` miscite note + the Phase 8 handoff).
- `05_symptoms.md`: grep `Symptom #`, `root cause`, `Handoff`; Read only the
  per-symptom root-cause lines and the Phase 8 handoff.
- `06_dry_solid.md`: grep `^### D6-`, `^### S6-`, `^### B6-`, `Governing E`,
  `Handoff`; Read only each finding's ID + one-line violation + governing E-NN
  + the handoff.
- `07_test_gaps.md`: grep `Coverage verdict`, `NO-PINNED-TEST`, `LOOSE-ONLY`,
  `BLOCKED-ON-OPEN-QUESTION`, `PRODUCER-UNKNOWN`, `PINNED-AGAINST`, `^### PT-`,
  `Handoff`; Read only the verdict tally, the anti-coverage roll-up, the
  meta-gap statement, the PT-NN list, the handoff.
- `09_open_questions.md`: grep `^## Q-`, `^## A-`, `STILL OPEN`, `CARRIED`;
  Read only the status line per Q/A and the Q-26 tail.

Stop condition: Part 8.A skeleton (the cluster IDs `CRIT-NN`/`HIGH-NN`/
`MED-NN`/`LOW-NN` with their `Subsumes` blocks and governing E-NN, no prose)
and the complete Part 8.B master reverse-index written to `08_findings.md`;
every section-1 source ID present exactly once; the 21-vs-20 DIVERGE and the
`F-044` miscite explicitly noted as P8-e reconciliation items; session ends.
Severity ordering of the cluster IDs is provisional and finalized in P8-e.

### P8-b -- The CRITICAL spine (symptoms + the two money CRITICALs + data-loss)

Goal: the full Part-8.A record for every CRITICAL cluster, each with severity
re-derived from the rubric against a cited displayed wrong dollar re-read this
session. These are the top of the report.

Clusters in scope (provisional from P8-a; the canonical mapping is the
reverse-index): the cross-page checking divergence (symptoms #1/#5; E-19/E-25),
the loan payment/principal drift (symptoms #2/#3/#4; E-18), the FICA SS-cap
bypass on the calibration path (F-037), the retirement phantom-income +
weighted-return overstatement (F-042), and the irreversible RECEIVED
hard-delete (Q-19/W-262, Refinement A).

Required reading (bounded):
- `financial_calculation_audit_plan.md:705-743`.
- The Part 8.A skeleton + Part 8.B already in `08_findings.md` (recovery state;
  read it, do not re-derive it).
- `05_symptoms.md`: Read the five symptom subsections in full (this is the
  spine; the worked dollars are re-read here, not re-derived).
- `03_consistency.md`: Read only F-001, F-008, F-009, F-013, F-014, F-015,
  F-016, F-026, F-037, F-042 and their `Verdict:` lines (the constituent
  bodies of the CRITICAL clusters) + `:6062-6075` (C3).
- `04_source_of_truth.md`: Read only the Family-A anchor and Family-B
  principal/rate sections and the drift register.
- `00_priors.md:184-213` (E-18, E-19), `:276-311` (E-25, E-27), the F-037 /
  F-042 lines, `:830` (PA-21), `:813-814` (PA-04/PA-05).
- Live `app/` source: re-resolve every CRITICAL cluster's `Evidence`
  `file:line` to current source (grep the function, Read the cited range);
  quote the key line this session (contract item 1). Specifically the
  `selectinload(Transaction.entries)` presence/absence sites, the
  `calculate_monthly_payment` call sites, `amortization_engine.py:950-978`, the
  `current_principal` settle-writer absence (grep-proven), `calibration_service.py`
  SS-cap path, the retirement zero-return truthiness sites, and the
  `templates.py` `hard_delete_template` guard.

Stop condition: every CRITICAL cluster carries every schema-3.1 element with
severity rubric-justified against a live-source-cited displayed wrong dollar;
symptoms #2/#3/#4 are ONE cluster (developer-directed); session ends.

### P8-c -- The HIGH tier

Goal: the full Part-8.A record for every HIGH cluster: structural drift not yet
symptomatic but realistic, the Phase-4 UNCLEAR stored columns (blocked on a
developer Q, recorded with the Q cited), the Phase-7 cross-page meta-gap (no
regression lock for the two worst symptoms), and the E-26 rounding-helper
absence (24 banker's-rounding sites).

Required reading (bounded):
- `financial_calculation_audit_plan.md:705-743`; the accumulated
  `08_findings.md` (recovery state).
- `03_consistency.md`: Read only the DIVERGE findings not consumed by P8-b
  (F-002, F-003, F-005, F-017, F-018, F-020, F-021, F-022, F-023, F-032, F-043,
  F-055) + their `Verdict:` lines.
- `04_source_of_truth.md`: Read only the UNCLEAR-column findings
  (`current_anchor_period_id`, the `effective_*_rate` quartet) and the
  Family-C/D risk columns.
- `06_dry_solid.md`: Read only the D6-NN findings and their governing E-NN.
- `07_test_gaps.md`: Read only the Part 7.B cross-page meta-gap statement and
  the Part 7.F.4 anti-coverage roll-up.
- `00_priors.md:286-296` (E-26), `:352-385` (E-12/E-15 family).
- Live `app/` source: re-resolve every HIGH cluster's `Evidence` to current
  source this session.

Stop condition: every HIGH cluster carries every schema-3.1 element; each
UNCLEAR-blocked one cites its open Q and is NOT resolved (contract item 5);
session ends.

### P8-d -- The MEDIUM / LOW tier + full PA-NN integration + standards + dead code

Goal: the remaining clusters (SOLID structure, the test-gap aggregate,
non-customer definition ambiguity, the E-16/E-17 Jinja/JS standards cluster
folding TA-01..TA-11 and the carried `loan/_escrow_list.html:37` site, the
F-040 dead-code LOW, the Phase-4 classification nit), and the explicit
reconciliation that **all thirty PA-01..PA-30 are integrated** (subsumed with a
citation into a cluster, or carried as their own finding) -- the line-806
obligation, proven in the reverse-index.

Required reading (bounded):
- `financial_calculation_audit_plan.md:705-743`; the accumulated
  `08_findings.md`.
- `00_priors.md:804-841` (PA-01..PA-30 -- Read in full; this session owns the
  integration proof) and `:386-452` (the W-NN non-HOLDS corpus).
- `06_dry_solid.md`: Read only the S6-NN findings, the B6-NN negative
  findings, and the carried E-16 standards-finding handoff line.
- `07_test_gaps.md`: Read only the non-COVERED Part 7.A verdict list and the
  PT-NN register.
- `01_inventory.md`: grep `Jinja`, `TA-0`, `client-side`, `recompute`; Read
  only the TA-01..TA-11 table and the JS-recompute list.
- `03_consistency.md`: Read only F-040 (dead code) and the W-NN cmp non-HOLDS
  roll-up.
- Live `app/` source: re-resolve every MED/LOW cluster's `Evidence` and spot
  the F-040 dead-code zero-consumer grep this session.

Stop condition: every remaining cluster carries every schema-3.1 element;
Part 8.B shows all PA-01..PA-30 with a `-> finding` or a
`subsumed-by <finding> (PA-NN cited in that finding's Subsumes)` entry; no PA
silently dropped; session ends.

### P8-e -- Verification and consolidation gate (trust-but-verify capstone)

No new findings, no new clusters. Verify, reconcile, sort, finalize.

Tasks (append Part 8.C):
1. **Spot-check:** >= 15 findings at random, weighted to CRITICAL, mixed tiers.
   For each: re-resolve the `Evidence` `file:line` to live source (grep/Read)
   AND re-derive the severity from the rubric. Show the table and the pass
   count. Threshold 100% on both axes; any miss (stale citation OR severity not
   rubric-supported) reopens that tier's session before the gate can pass.
2. **Surjection reconciliation (G4):** mechanically confirm every section-1
   source ID is in Part 8.B exactly once; zero orphans; zero double-maps;
   every PA-01..PA-30 present; every Phase-3 DIVERGE and Phase-4 UNCLEAR maps
   to a `-> finding` (never `NOT-A-FINDING`); every Phase-7 non-COVERED concept
   accounted for.
3. **Cross-phase reconciliation (G6):** resolve the Phase-3-C1-21 vs
   Phase-7-7.B-20 DIVERGE count (enumerate both sets, name the delta finding,
   state which is correct or record it as a finding against the prior phase);
   confirm the `F-044` miscite is carried corrected not propagated; confirm the
   stale 470-line `savings.py:dashboard` tag is carried as Phase 6's corrected
   state; confirm Phase 8's symptom->finding map matches Phase 5 C2 / Phase 4
   drift register or record the contradiction as a finding against the prior
   phase.
4. **Severity sort + ID finalization:** sort Part 8.A by severity, then by the
   developer's reported symptoms within CRITICAL (audit-plan 8); finalize the
   `CRIT/HIGH/MED/LOW-NN` numbers; confirm the IDs now encode the mandated
   sort.
5. **Acceptance gate** (section 5), each G1-G9 with evidence/verdict.
6. **Handoff:** what Phase 9 inherits -- the Q-26 NULL-semantics tail carried
   unchanged; any UNKNOWN/UNCLEAR/BLOCKED finding whose blocking Q the
   developer must answer before remediation; the explicit statement that
   remediation planning is a separate post-audit exercise (audit-plan 8 last
   paragraph, section 11).
7. `git status` pasted, showing only `docs/audits/financial_calculations/`.

Stop condition: Part 8.C appended; "Phase 8 complete" recorded with the G1-G9
roll-up, or, if any gate fails, the failing criterion and the session to reopen
named, and the session stops without declaring completion.

## 5. Phase 8 acceptance gate (mirrors Phase 5 / 6 / 7 section 5)

Phase 8 is complete only when all hold, each with shown evidence:

- **G1** `08_findings.md` exists, non-empty; Part 8.A (per-finding records),
  Part 8.B (master reverse-index), Part 8.C (gate); every finding carries every
  schema-3.1 element; Part 8.A sorted by severity then by reported symptom.
- **G2** Every finding's `Evidence` `file:line` was re-resolved to live source
  during a Phase 8 session with the key line quoted; no citation carried from a
  phase doc without re-resolution; any failed re-resolution recorded as a
  finding against the prior phase with both locations.
- **G3** Every `CRIT-NN` cites the displayed wrong-dollar figure (or, for the
  data-loss class, the irreversible-destruction path), the page + `file:line`
  where it renders, and the hand-figure or the Phase-3/5 worked example re-read
  this session; severity derived from the rubric, not copied from C3; every
  C3 candidate re-tested and every non-candidate checked for a missed CRITICAL.
- **G4** The master reverse-index is complete: every section-1 source ID maps
  to exactly one finding or to `NOT-A-FINDING` with a closed-set reason; zero
  orphans; zero double-maps; all PA-01..PA-30 integrated; every Phase-3 DIVERGE
  and Phase-4 UNCLEAR maps to a finding.
- **G5** Spot-check >= 15 findings (mixed tiers, CRITICAL-weighted), 100%
  re-resolve to live source AND re-derive to the recorded severity tier; table
  and count shown.
- **G6** Cross-phase reconciliation done: the DIVERGE count discrepancy, the
  `F-044` miscite, the stale 470-line tag, and the symptom->finding map vs
  Phase 5 C2 / Phase 4 drift register each reconciled or recorded as a finding
  against the prior phase with both citations.
- **G7** Every finding has exactly one remediation-direction sentence
  consistent with its governing E-NN; no fix diff produced (or, if one was, it
  was reverted and recorded as the remediation-direction sentence per
  audit-plan 10.6).
- **G8** No new auditor-invented finding in `08_findings.md`; any genuinely new
  ambiguity recorded in `09_open_questions.md` with where it arose; every
  UNKNOWN/UNCLEAR/BLOCKED finding preserved with its blocking Q, not resolved;
  the Q-26 NULL-semantics tail carried to Phase 9 unchanged.
- **G9** `git status` shows only `docs/audits/financial_calculations/` files
  changed; no source, test, migration, template, or JS file touched.

## 6. Anti-shortcut prompt (paste at the top of every Phase 8 session)

> This session is part of a read-only audit running in Claude Code's `plan`
> permission mode. Document findings in `08_findings.md`. Phase 8 aggregates
> proven Phase 1-7 findings into a severity-sorted, root-cause-clustered
> report; it does not re-audit, does not re-derive settled verdicts, and does
> not resolve open questions. Re-resolve every cited `file:line` to LIVE source
> this session and quote the key line -- never trust a citation copied from a
> phase doc; a stale pointer in this document is the worst-placed one in the
> audit. Derive every severity from the audit-plan rubric against a cited money
> impact; the C3 CRITICAL pre-list is an input to re-test, not an authority to
> copy; every CRITICAL names a displayed wrong dollar (or an irreversible
> data-loss path) with the page and `file:line` where it renders. Prove the
> cluster map is a surjection: every source ID maps to exactly one finding or
> to NOT-A-FINDING with a reason; zero orphans; all PA-01..PA-30 integrated.
> Add no new finding; a genuinely new ambiguity goes to `09_open_questions.md`,
> never here. Keep UNKNOWN/UNCLEAR/BLOCKED findings unresolved with their
> blocking Q cited. Exactly one remediation-direction sentence per finding,
> consistent with its governing E-NN -- never a fix, never a diff. Surface
> every cross-phase discrepancy in the open; do not smooth it. Findings go into
> `08_findings.md`; source files, tests, and migrations remain untouched. Stay
> within the assigned session's tier.

## 7. Failure modes and remedies

- Kitchen-sink session -> one tier per session; `/clear` between; the bounded
  reading lists in section 4.
- Re-audit drift -> Phase 8 consumes settled verdicts and locked E-NN; if a
  session starts re-deriving whether a number is wrong or re-litigating an
  answered Q, stop -- the deliverable is the prioritized clustered summary, not
  a second Phase 3/5.
- Severity-by-vibe -> the load-bearing rule (contract item 2, G3): a CRITICAL
  with no cited displayed wrong dollar re-read this session is downgraded; the
  P8-e spot-check re-derives a sample from the rubric and any miss reopens the
  tier.
- Trust-then-verify gap (the audit's own trap, here at the worst place) ->
  every citation re-resolved to live source this session; the P8-e spot-check
  is mandatory; a failed re-resolution is a finding against the prior phase,
  not a quiet fix.
- Lost finding via over-clustering -> the surjection (contract item 3, G4): no
  cluster boundary is valid until every member source ID and every PA-NN
  appears exactly once in the reverse-index.
- Discrepancy-smoothing -> the 21-vs-20 DIVERGE, the F-044 miscite, the stale
  tag are reconciled in the open (G6); averaging a discrepancy to make the
  report tidy is the exact failure the audit exists to surface.
- Fix temptation -> one remediation-direction sentence, governed by the E-NN
  that already states the end state; a diff is reverted and recorded as that
  sentence (audit-plan 10.6/10.7).
- Scope drift into Phase 9 -> Phase 8 does not answer Q-26 or any open Q; it
  records the blocked finding and carries the question; resolving it is Phase 9.

## 8. Ready-to-paste session prompts

Run strictly in order (P8-a -> P8-b -> P8-c -> P8-d -> P8-e), each in its own
session started with `claude --permission-mode plan`, with `/clear` between. Do
not use `@` on the large audit docs (`00`..`07`, `09`) -- `@` reads the whole
file and blows context; the prompts instruct ranged Reads and greps instead.
Each session's recovery state is the accumulated `08_findings.md` (audit-plan
10.5). The anti-shortcut preamble (section 6) prefixes every prompt verbatim;
it is referenced as "[anti-shortcut preamble]" in P8-b..P8-e to keep this file
readable. At paste time, expand it.

### Prompt P8-a (cluster map + master reverse-index)

```text
This session is part of a read-only audit running in Claude Code's `plan`
permission mode. Document findings in
docs/audits/financial_calculations/08_findings.md. Phase 8 aggregates proven
Phase 1-7 findings into a severity-sorted, root-cause-clustered report; it does
not re-audit, does not re-derive settled verdicts, and does not resolve open
questions. Re-resolve every cited file:line to LIVE source this session and
quote the key line -- never trust a citation copied from a phase doc. Derive
every severity from the audit-plan rubric against a cited money impact; the C3
CRITICAL pre-list is an input to re-test, not an authority to copy. Prove the
cluster map is a surjection: every source ID maps to exactly one finding or to
NOT-A-FINDING with a reason; zero orphans; all PA-01..PA-30 integrated. Add no
new finding; a genuinely new ambiguity goes to 09_open_questions.md, never
here. Keep UNKNOWN/UNCLEAR/BLOCKED findings unresolved with their blocking Q.
One remediation-direction sentence per finding, governed by its E-NN, never a
fix. Surface every cross-phase discrepancy in the open. Findings go into
08_findings.md; source/tests/migrations untouched.

This is Phase 8 session P8-a. Follow phase8_plan.md section 4 (P8-a), the
trust-but-verify contract in section 2, and the schemas in sections 3.1-3.3.
Scope: build the cluster map and the COMPLETE master reverse-index only -- no
severity finalization, no prose paragraphs, no remediation sentences. The Phase
8 finding-ID scheme is CRIT-NN / HIGH-NN / MED-NN / LOW-NN with a traceability
matrix (the audit-plan "ID (F-001...)" instruction collides with Phase 3's live
F-001..F-056; record that as an audit-plan-vs-execution divergence). The output
path is docs/audits/financial_calculations/08_findings.md (plural "audits"; the
audit-plan singular path is a typo -- note it, do not create the wrong dir).

Bounded reading (grep for the anchor, Read only the named range; do not widen
without recording why; never `@` a large doc):
- docs/audits/financial_calculations/financial_calculation_audit_plan.md lines
  705-743 and 1005-1032.
- 00_priors.md lines 154-329 (E-01..E-28) and 804-841 (PA-01..PA-30).
- 03_consistency.md: grep "^### Finding F-" / "^## Finding F-" / "Verdict:";
  Read only the C1 consolidated register and lines 6049-6075 (C2 + C3). Do not
  read finding bodies.
- 04_source_of_truth.md lines 2090-2270 (classification table + drift register
  + F-044 miscite note + Phase 8 handoff).
- 05_symptoms.md: grep "Symptom #" / "root cause" / "Handoff"; Read only the
  per-symptom root-cause lines and the Phase 8 handoff.
- 06_dry_solid.md: grep "^### D6-" / "^### S6-" / "^### B6-" / "Governing E" /
  "Handoff"; Read only each finding ID + one-line violation + governing E-NN +
  the handoff.
- 07_test_gaps.md: grep "Coverage verdict" / "NO-PINNED-TEST" / "LOOSE-ONLY" /
  "BLOCKED-ON-OPEN-QUESTION" / "PRODUCER-UNKNOWN" / "PINNED-AGAINST" / "^### PT-"
  / "Handoff"; Read only the verdict tally, anti-coverage roll-up, meta-gap
  statement, PT-NN list, handoff.
- 09_open_questions.md: grep "^## Q-" / "^## A-" / "STILL OPEN" / "CARRIED";
  Read only the status line per Q/A and the Q-26 tail.

Produce in 08_findings.md: a "Part 8.A -- cluster skeleton" section (the
CRIT/HIGH/MED/LOW-NN cluster IDs with provisional severity tier, governing
E-NN, and the full Subsumes traceability block per cluster -- NO prose, NO
remediation sentence) and the COMPLETE "Part 8.B -- master reverse-index"
(every section-1 source ID -> exactly one cluster or NOT-A-FINDING with a
closed-set reason). Explicitly list the 21-vs-20 DIVERGE count and the F-044
miscite as P8-e reconciliation items. Do not run the app or pytest. Do not
modify code or tests. End by writing the two sections and stopping; paste `git
status` confirming only docs/audits/financial_calculations/ changed.
```

### Prompt P8-b (CRITICAL spine)

```text
[anti-shortcut preamble]

This is Phase 8 session P8-b. Follow phase8_plan.md section 4 (P8-b), the
contract in section 2, the schema in section 3.1, and the rubric in section
3.3. Scope: the full Part-8.A record for every CRITICAL cluster only --
provisionally the cross-page checking divergence (symptoms #1/#5, E-19/E-25),
the loan payment/principal drift (symptoms #2/#3/#4 as ONE cluster, E-18), the
FICA SS-cap calibration bypass (F-037), the retirement phantom-income +
weighted-return overstatement (F-042), and the irreversible RECEIVED
hard-delete (Q-19/W-262). The canonical membership is Part 8.B already written.

Bounded reading:
- financial_calculation_audit_plan.md lines 705-743.
- The Part 8.A skeleton + Part 8.B in 08_findings.md (recovery state; read,
  do not re-derive).
- 05_symptoms.md: the five symptom subsections in full.
- 03_consistency.md: Read only F-001, F-008, F-009, F-013, F-014, F-015,
  F-016, F-026, F-037, F-042 + their Verdict lines + lines 6062-6075 (C3).
- 04_source_of_truth.md: Read only Family-A anchor, Family-B principal/rate,
  and the drift register.
- 00_priors.md lines 184-213 (E-18, E-19), 276-311 (E-25, E-27), the F-037 and
  F-042 expectation lines, 813-814 (PA-04/PA-05), 830 (PA-21).
- Live app/ source: re-resolve every CRITICAL cluster's Evidence file:line to
  current source (grep the function, Read the cited range, quote the key line)
  -- the selectinload(Transaction.entries) sites, the calculate_monthly_payment
  call sites, amortization_engine.py:950-978, the current_principal
  settle-writer absence (grep-prove), the calibration SS-cap path, the
  retirement zero-return truthiness sites, templates.py hard_delete_template.

Produce in 08_findings.md the CRITICAL cluster records in Part 8.A, each with
every schema-3.1 element and severity rubric-justified against a
live-source-cited displayed wrong dollar re-read this session. Symptoms
#2/#3/#4 are ONE cluster. Do not run the app or pytest. Do not modify code or
tests. End by writing the records and stopping; paste `git status`.
```

### Prompt P8-c (HIGH tier)

```text
[anti-shortcut preamble]

This is Phase 8 session P8-c. Follow phase8_plan.md section 4 (P8-c), the
contract in section 2, schema 3.1, rubric 3.3. Scope: the full Part-8.A record
for every HIGH cluster only -- structural/computed drift not yet symptomatic,
the Phase-4 UNCLEAR stored columns (recorded with the blocking Q cited, NOT
resolved), the Phase-7 cross-page regression meta-gap, the E-26 rounding-helper
absence. Canonical membership is Part 8.B.

Bounded reading:
- financial_calculation_audit_plan.md lines 705-743; the accumulated
  08_findings.md (recovery state).
- 03_consistency.md: Read only the DIVERGE findings not consumed by P8-b
  (F-002, F-003, F-005, F-017, F-018, F-020, F-021, F-022, F-023, F-032, F-043,
  F-055) + their Verdict lines.
- 04_source_of_truth.md: Read only the UNCLEAR-column findings
  (current_anchor_period_id, the effective_*_rate quartet) and the Family-C/D
  risk columns.
- 06_dry_solid.md: Read only the D6-NN findings and governing E-NN.
- 07_test_gaps.md: Read only the Part 7.B cross-page meta-gap and the Part
  7.F.4 anti-coverage roll-up.
- 00_priors.md lines 286-296 (E-26), 352-385 (E-12/E-15 family).
- Live app/ source: re-resolve every HIGH cluster's Evidence to current source
  this session and quote the key line.

Produce the HIGH cluster records in Part 8.A, each with every schema-3.1
element; each UNCLEAR-blocked one cites its open Q and is NOT resolved. Do not
run the app or pytest. Do not modify code or tests. End by writing the records
and stopping; paste `git status`.
```

### Prompt P8-d (MEDIUM / LOW + PA integration + standards + dead code)

```text
[anti-shortcut preamble]

This is Phase 8 session P8-d. Follow phase8_plan.md section 4 (P8-d), the
contract in section 2, schema 3.1, rubric 3.3. Scope: the remaining MEDIUM/LOW
clusters (SOLID structure, the test-gap aggregate, non-customer definition
ambiguity, the E-16/E-17 Jinja/JS standards cluster folding TA-01..TA-11 and
the carried loan/_escrow_list.html:37 site, F-040 dead-code LOW, the Phase-4
classification nit) AND the explicit proof that all thirty PA-01..PA-30 are
integrated in Part 8.B (line-806 obligation). Canonical membership is Part 8.B.

Bounded reading:
- financial_calculation_audit_plan.md lines 705-743; the accumulated
  08_findings.md.
- 00_priors.md lines 804-841 (PA-01..PA-30 in full -- this session owns the
  integration proof) and 386-452 (W-NN non-HOLDS corpus).
- 06_dry_solid.md: Read only the S6-NN findings, the B6-NN negative findings,
  and the carried E-16 standards-finding handoff line.
- 07_test_gaps.md: Read only the non-COVERED Part 7.A verdict list and the
  PT-NN register.
- 01_inventory.md: grep "Jinja" / "TA-0" / "client-side" / "recompute"; Read
  only the TA-01..TA-11 table and the JS-recompute list.
- 03_consistency.md: Read only F-040 and the W-NN cmp non-HOLDS roll-up.
- Live app/ source: re-resolve every MED/LOW cluster's Evidence and the F-040
  zero-consumer grep this session.

Produce the remaining cluster records in Part 8.A; update Part 8.B so every
PA-01..PA-30 shows a "-> finding" or "subsumed-by <finding>" entry with no PA
dropped. Do not run the app or pytest. Do not modify code or tests. End by
writing the records and stopping; paste `git status`.
```

### Prompt P8-e (verification and consolidation gate)

```text
This session is part of a read-only audit running in Claude Code's `plan`
permission mode. Verify every factual claim by re-resolving the cited
file:line to live source; do not recall from memory and do not trust a prior
session's citation without re-opening it. No new findings, no new clusters. The
gate goes into docs/audits/financial_calculations/08_findings.md;
source/tests/migrations untouched and pytest is never invoked.

This is Phase 8 session P8-e, the trust-but-verify capstone. Follow
phase8_plan.md section 4 (P8-e) and the acceptance gate in section 5. Read the
full docs/audits/financial_calculations/08_findings.md, phase8_plan.md sections
1-5, re-grep 03_consistency.md for "Verdict:" (enumerate the DIVERGE set for
the 21-vs-20 reconciliation), and re-read 05_symptoms.md C2 + 04_source_of_truth.md
lines 2129-2146 (drift register) for the symptom-map reconciliation.

Do exactly these tasks, appending a "Part 8.C" gate section:
1. Spot-check >= 15 findings at random, CRITICAL-weighted, mixed tiers;
   re-resolve each Evidence file:line to live source AND re-derive its severity
   from the rubric; show the table and pass count. Threshold 100% on both axes;
   any miss reopens that tier's session before the gate passes -- record and
   stop.
2. Surjection reconciliation: every section-1 source ID in Part 8.B exactly
   once; zero orphans; zero double-maps; all PA-01..PA-30 integrated; every
   Phase-3 DIVERGE and Phase-4 UNCLEAR maps to a "-> finding".
3. Cross-phase reconciliation: resolve Phase-3-C1-21 vs Phase-7-7.B-20 DIVERGE
   (enumerate both, name the delta); confirm F-044 carried corrected; confirm
   the stale 470-line tag carried as Phase 6's corrected state; confirm Phase
   8's symptom->finding map matches Phase 5 C2 / Phase 4 drift register, or
   record the contradiction as a finding against the prior phase.
4. Severity sort + ID finalization: sort Part 8.A by severity then by reported
   symptom within CRITICAL; finalize the CRIT/HIGH/MED/LOW-NN numbers.
5. Acceptance gate G1-G9 from phase8_plan.md section 5, each with
   evidence/verdict.
6. Handoff to Phase 9: the Q-26 NULL-semantics tail carried unchanged; every
   UNKNOWN/UNCLEAR/BLOCKED finding whose blocking Q the developer must answer
   before remediation; the explicit statement that remediation planning is a
   separate post-audit exercise.
7. Paste `git status` confirming only docs/audits/financial_calculations/
   changed; confirm no source/test/migration/template/JS file was touched.

End by recording "Phase 8 complete" with the G1-G9 roll-up, or, if any gate
fails, name the failing criterion and the session to reopen and stop without
declaring completion.
```
