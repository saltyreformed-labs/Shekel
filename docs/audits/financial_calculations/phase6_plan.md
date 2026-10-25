> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Phase 6 Execution Plan -- DRY and SOLID Audit

Meta-plan for executing Phase 6 of the financial-calculation audit. This file is
the launch script: each session below is started by pasting its ready-to-paste
prompt (section 8) verbatim. This file is a planning artifact, not a phase
output and not source code. The phase output is `06_dry_solid.md`.

Authoritative spec: `financial_calculation_audit_plan.md` section 6 (lines
614-676) and section 10 (how to run). This file does not override the audit
plan; it sequences it and binds the trust-but-verify contract the developer
requires. Where the audit plan and this file appear to differ, the audit plan
wins and the divergence is a defect in this file to be reported, not worked
around.

## 0. Why Phase 6 is different now

Phase 6 is the first **structure** phase. Phases 1-5 asked "does the number come
out right?" Phase 6 asks "is the calculation written once, in one place, behind
one interface?" The two are not independent: the audit's central finding across
all five symptoms is that there is no canonical producer for the core figures,
and **duplication is the substrate on which the silent drift in Phases 3-5
grew** (`financial_calculation_audit_plan.md:618`). Phase 6 converts that thesis
from narrative into a file/line-cited duplication and boundary map.

Three things make Phase 6 different from a generic DRY/SOLID pass:

1. **The intended single-source-of-truth is already locked, not open.** E-18
   (one event-derived loan resolver), E-19 (one date-anchored anchor resolver),
   E-24 (one canonical loan-obligation aggregator + deduplicated 26/12 factor),
   E-25 (one period-subtotal producer sharing `_entry_aware_amount` with the
   balance calculator), E-26 (one centralized money-rounding helper with named
   sanctioned variants), E-27 (one "balance as of date" path) were resolved by
   the developer 2026-05-18 and recorded in `00_priors.md` 0.3. Phase 6 does not
   re-ask "what should the single source be." It documents *where the code
   currently duplicates the thing E-NN says must be singular*, recommends the
   consolidation E-NN already fixes, and stops. Inventing a new "obvious"
   single-source target that no E-NN states is prohibited (audit-plan 0.3); it
   goes to `09_open_questions.md` only if it is a genuinely new ambiguity, never
   as a silent assumption.

2. **Phase 3 and Phase 4 pre-tagged the register, and that register is a
   hypothesis to falsify, not a checklist to copy.** `03_consistency.md` carries
   ~42 inline `Phase-6` DRY/SRP notes; `04_source_of_truth.md` carries ~24.
   Phase 6 re-opens and re-greps every one at source this phase. It must also
   find the duplications Phase 3 did **not** tag: a tag the prior phase missed
   is exactly the silent-drift substrate the audit exists to surface. The
   completeness of Phase 3's tagging is itself under audit here.

3. **The audit plan explicitly forbids trusting the roadmap on SRP/OCP.**
   "the prior audit identified some of these and the roadmap states they are
   addressed, so the audit must verify the current state by grep, not by trust"
   (`financial_calculation_audit_plan.md:656-657`, restated :650-652 for the
   470-line `savings.py:dashboard` SRP claim). Several account-parameter
   watchlist rows (W-021, W-022, W-026, W-038, W-039 in `00_priors.md` 0.4) are
   `planned-per-plan`, not `complete-per-plan` -- meaning hardcoded type-ID
   dispatch may still be live. Phase 6 proves the current state from `grep`,
   never from a roadmap "addressed."

One tail item is carried, not resolved here: A-26 / E-26 explicitly state the
`savings_goal` `ROUND_CEILING` site is **NOT** a finding, and E-28 states the
`accounts.current_anchor_balance` / `account_anchor_history.anchor_balance`
absent range CHECK is the one sanctioned domain exception. Phase 6 honors these
locked exceptions and does not relitigate them.

## 1. Inherited spine (a falsifiable register, not scaffolding)

Phase 6 inherits a concrete duplication register from three sources. Each entry
is re-proven from source this phase (section 2 contract). A register entry that
does not resolve at source, or that resolves to *more* clone sites than the tag
claims, is itself a Phase 6 finding against the prior phase (audit-plan 10.8
trust-then-verify remedy).

| Root | E-NN | What must be singular | Carried clone evidence to re-verify | Source of carry |
| --- | --- | --- | --- | --- |
| Loan resolver | E-18 | One event-derived resolver replaying confirmed payments from the latest anchor | >=4 surfaces each assemble their own `(P,r,n)` for `calculate_monthly_payment`; the 16-site incompatible-triple register (F-013) | `05_symptoms.md:1703-1713`; `03_consistency.md` F-013 |
| Anchor resolver | E-19 | One date-anchored anchor resolver; no per-page anchor-None fallback | Per-page anchor base divergence (F-001/F-008); calendar zero short-circuit | Phase 5 handoff; F-001/F-008 |
| Period subtotal | E-25 | One period-subtotal producer sharing `_entry_aware_amount` with the balance calculator | Grid subtotal computed on raw `effective_amount` (`grid.py:263-279`) not the entries-aware base | `00_priors.md` E-25; `05_symptoms.md:1711` |
| Balance-as-of-date | E-27 | One canonical entries-aware "balance as of date D" path | Calendar month-end path is a second non-entries-aware balance path (F-003/F-009) | `00_priors.md` E-27 |
| Loan-obligation aggregator | E-24 | One canonical obligation aggregator; the `26/12` conversion factor declared once | `26/12` factor duplicated (`03_consistency.md:2796` and the DTI-mortgage double-count register); one obligation, two unreconciled representations | `00_priors.md` E-24 |
| Money-rounding helper | E-26 | One helper with named sanctioned variants; full-precision intermediates | 19-file `TWO_PLACES` constant redeclaration; 24 monetary `.quantize()` sites silently using banker's rounding | `00_priors.md` E-26 (lines 286-296) |
| effective_amount mirror | (E-25 family) | One effective-amount accessor | 4 hand-rolled 2-tier `actual if not None else estimated` mirrors (S10/S14/T1/T4); plus every other inline reproduction across services/routes/templates/JS | `03_consistency.md:2227-2267`; P3-c sweep |
| Status-filter inline | (E-15) | Centralized status predicate, not inline `status_id != projected_id` | Inline status comparisons reproduced across many files | audit-plan 6.1 lines 637-639 |
| Magic-number fallbacks | (PA-05) | Named constants with source citations | `0.04`, `4.0`, `7.0` SWR / assumed-return fallbacks | `00_priors.md` PA-05 (line 814) |
| Per-account dispatcher | (SRP/OCP) | One per-account-type calculator dispatcher | Dual dispatcher `savings_dashboard_service.py:294` vs `year_end_summary_service.py:2036` | `05_symptoms.md:1708-1710` |
| Helper split | (DRY) | Parameterized core for the `_sum_*` family | `_sum_remaining` vs `_sum_all` share most structure, vary by filter (F-010) | `03_consistency.md:727-768` |

This register is the **starting set, not the closing set.** Section 4 sessions
must also enumerate untagged duplications discovered by the mandated grep sweeps.

## 2. The trust-but-verify contract (binds every Phase 6 session)

1. **Every duplication claim is grepped this session.** "X is computed in N
   places" requires the `grep`/`glob` command and every one of the N
   `file:line` hits pasted into `06_dry_solid.md`. A claim of "N places"
   supported by fewer than N citations is not a finding; it is an incomplete
   one and is marked so.
2. **Helpers are expanded inline before comparison.** Two helpers with the same
   name in different files can have different bodies; two with different names
   can have identical bodies (`financial_calculation_audit_plan.md:441-444`).
   Every "these duplicate" claim shows both expanded forms (the relevant lines
   Read this session), not just the two call sites.
3. **SRP/OCP claims are proven by grep, never trusted from the roadmap.** The
   470-line `savings.py:dashboard` claim, every OCP "addressed" claim, and every
   `complete-per-plan` watchlist row touching dispatch are re-derived from the
   live file's current line count and current branch structure
   (`financial_calculation_audit_plan.md:650-657`). The roadmap is not evidence.
4. **The Phase-3/Phase-4 `Phase-6` tag register is a hypothesis to falsify.**
   Each tag is re-opened at the live source line this session. A tag that no
   longer resolves, resolves to a different construct, or undercounts the clone
   set is recorded as a divergence against the prior phase with both citations.
5. **No fix is written.** Phase 6 recommends a single source of truth *in the
   report only* (`financial_calculation_audit_plan.md:642`). A diff produced
   during a session is a finding artifact, not a fix: extract the diagnosis,
   record it as a "remediation direction" sentence, revert (audit-plan 10.6).
6. **Locked E-NN intent is consumed, not relitigated.** Each duplication root is
   stated against its governing E-NN with the consolidation that E-NN already
   fixes. No new auditor-invented "obvious" single-source target is added to
   `09_open_questions.md`; only genuinely new ambiguities, if any (audit-plan
   0.3, mirrors Phase 5 G8). The E-26 `ROUND_CEILING` exception and the E-28
   anchor-balance CHECK exception are honored, not reopened.
7. **Read-only. Plan permission mode.** No app run, no code/test/migration/
   template/JS edit. Output is `06_dry_solid.md` only; `git status` must show
   only `docs/audits/financial_calculations/` at every session end.
8. **Explore subagent for the broad sweeps is mandatory, not optional**
   (`financial_calculation_audit_plan.md:990-994`, section 10.1c). The
   per-pattern repository sweeps in sections 4 (P6-a2, P6-b) run inside Explore
   so raw file contents stay out of the main session; the main session
   aggregates the returned `file:line` register.

## 3. Per-finding schema

`06_dry_solid.md` has three top-level parts mirroring audit-plan 6.1 / 6.2 /
6.3. Every finding, regardless of part, carries:

- **ID** -- `D6-NN` (DRY), `S6-NN` (SOLID), `B6-NN` (boundary).
- **Principle** -- the exact principle violated (DRY; SRP; OCP; LSP; ISP; DIP;
  the Routes->Services->Models boundary; Transfer Invariant 5) with the
  audit-plan or coding-standards line that states it.
- **Sites** -- every `file:line` where the duplicated/violating construct
  appears, each Read this session, with the relevant lines quoted or the
  expanded helper form shown (contract items 1-2).
- **Expanded comparison** (DRY only) -- the two-or-more forms placed
  side by side so the reader sees they are the same calculation, not just
  similarly named.
- **Governing E-NN** -- the locked expectation that already fixes this (E-18,
  E-19, E-24, E-25, E-26, E-27, or, for the standards-level ones, E-15/E-10
  family / PA-05), or `NONE -> structural-only` if no E-NN governs (then it is
  a pure structure finding, not a correctness one).
- **Recommended single source of truth** -- the consolidation, *report-only*,
  one to three sentences, consistent with the governing E-NN. No diff.
- **Inherited-vs-independent note** -- whether this was carried from the section
  1 register / a Phase-3 `Phase-6` tag and confirmed, or is newly found this
  phase, or contradicts the prior tag (with both citations).
- **Blast radius** -- one sentence: which displayed figure drifts if these
  sites disagree, cross-linked to the Phase 3/4/5 finding that already observed
  it (or "no observed drift yet" for a latent structural one).

SOLID findings additionally carry the live metric proving the violation (the
function's current line count for an SRP finding; the current `enum`/string
branch for an OCP finding; the parameter-bag field-usage ratio for an ISP
finding). Boundary findings additionally carry the grep that proves presence
(a forbidden import) or the grep that proves absence (no second writer of
shadows / no second `budget.transfers` balance reader).

## 4. Sessions

One concern-family per session. `/clear` between sessions. Required reading is
line-range-bounded to prevent the infinite-exploration / context-blowout
failure mode. Read the live `app/` source in full for any function before
drawing a conclusion about it (CLAUDE.md rule 2; audit-plan hard rule 3). The
large audit docs (`01`/`02`/`03`/`04`) are **never** read whole and never
referenced with `@`; sessions grep them for the named anchors and Read only the
bounded ranges below.

### P6-a1 -- DRY: the canonical-producer-absence family (E-18/E-19/E-24/E-25/E-27)

Goal: prove, at live source, that the figures E-18/E-19/E-24/E-25/E-27 each say
must have one producer in fact have several, and that the several disagree on
the dimensions Phase 3 compared. This is the spine of the DRY part: it is the
structural statement of the same divergence Phases 3-5 observed numerically.

Required reading (bounded):
- `financial_calculation_audit_plan.md:614-643` (Phase 6 intro + 6.1).
- `00_priors.md:260-311` (E-24, E-25, E-27) and `:184-213` (E-18, E-19).
- `05_symptoms.md:1701-1740` (Phase 5 handoff to Phase 6).
- `03_consistency.md:727-768` (F-010 `_sum_remaining` vs `_sum_all`),
  `:109-213` (F-001), `:580-648` (F-008), `:283-340` (F-003), `:649-726`
  (F-009), `:1009-1149` (F-013). Grep `03_consistency.md` for
  `Phase-6` / `DRY note` / `SRP note` and enumerate every hit's line; Read
  only the surrounding finding for each, not the whole file.
- `04_source_of_truth.md`: grep for `Phase-6` / `DRY` / `SRP` and enumerate
  every hit; Read only `:2129-2146` (drift register) and the surrounding
  paragraph of each hit.
- Live source, Read in full at the relevant functions: the `(P,r,n)`-assembling
  surfaces for `calculate_monthly_payment` (`amortization_engine.py`,
  `loan.py`, `debt_strategy.py`, `savings_dashboard_service.py`,
  `year_end_summary_service.py`, `dashboard_service.py` -- grep first for
  `calculate_monthly_payment(` to get the exact call set);
  `balance_calculator.py` `_sum_remaining` / `_sum_all` / `_entry_aware_amount`;
  the grid subtotal site (`grid.py`, grep `subtotal`); the calendar month-end
  path (`calendar_service.py`); the `26/12` factor sites (grep
  `26 / 12` / `Decimal("26")` / `PAY_PERIODS_PER_YEAR` across `app/`); the dual
  per-account dispatcher (`savings_dashboard_service.py` ~294,
  `year_end_summary_service.py` ~2036).

Stop condition: the canonical-producer-absence DRY findings (`D6-` series for
loan resolver, anchor resolver, period subtotal, balance-as-of-date,
obligation aggregator, the `_sum_*` split) written to `06_dry_solid.md`, each
with every section-3 element and every clone site grepped this session;
session ends.

### P6-a2 -- DRY: the cross-cutting micro-duplications (E-26, effective_amount, status filter, magic numbers)

Goal: the repository-wide sweeps. These are mechanical and high-count, so they
run through the Explore subagent (contract item 8). Each sweep returns a
`file:line` register the main session folds into findings.

Required reading (bounded):
- `financial_calculation_audit_plan.md:624-643` (6.1, the inline-duplication
  bullets specifically 637-639).
- `00_priors.md:286-296` (E-26), `:814` (PA-05), `:352-385` (E-10..E-17
  standards-derived expectations -- the effective_amount / Decimal / ID-based
  family).
- `03_consistency.md:2110-2267` (P3-c effective_amount sweep, the S10/S14/T1/T4
  mirror table). Grep `03_consistency.md` and `02_concepts.md` for
  `effective_amount` and enumerate the consumer register; Read only the bounded
  finding around each, not the whole file.
- `01_inventory.md`: grep for `quantize` / `TWO_PLACES` / `ROUND_HALF` /
  `effective_amount` / `actual_amount` / `estimated_amount` to seed the sweep
  target list; do not read whole.
- Live source via Explore sweeps (one Explore invocation per pattern,
  thoroughness `very thorough`): `grep -rn 'quantize'`,
  `grep -rn 'TWO_PLACES\|Decimal("0.01")'`, `grep -rn 'ROUND_HALF\|ROUND_CEIL'`,
  `grep -rn 'actual_amount if .* else .*estimated_amount'` (and the Jinja and JS
  equivalents under `app/templates/` and `app/static/js/`),
  `grep -rn 'status_id != \|status_id == \|!= projected\|== projected'`,
  `grep -rn '0\.04\|4\.0\|7\.0\|0\.062\|0\.0145'` scoped to retirement/growth
  fallback sites. The main session Reads the live lines for the E-26 site
  classification (which `quantize` sites are monetary boundary vs intermediate;
  which are the sanctioned `ROUND_CEILING` `savings_goal` exception that is
  explicitly NOT a finding per E-26).

Stop condition: the `D6-` micro-duplication findings (rounding-helper absence +
the 19-file `TWO_PLACES` redeclaration + 24 banker's-rounding sites classified;
the effective_amount mirror inventory; the inline-status-filter inventory; the
PA-05 magic-number inventory) written with full grepped registers and the E-26
sanctioned exception explicitly excluded; session ends.

### P6-b -- SOLID: SRP, OCP, LSP, ISP, DIP

Goal: the service-design audit, with the audit plan's explicit "grep, not
trust" mandate on SRP/OCP front and center.

Required reading (bounded):
- `financial_calculation_audit_plan.md:644-669` (6.2, all five principles).
- `00_priors.md:122-149` (standards rules on calc code -- the 50/100/200-line
  thresholds), `:386-452` (watchlist W-001..W-052; note which dispatch rows are
  `planned-per-plan` vs `complete-per-plan`).
- `01_inventory.md`: grep for each service/route file's recorded function list;
  do not read whole.
- Live source: for SRP, `wc -l` every file in `app/services/` and `app/routes/`,
  then Read in full every function over 200 lines (start with the known
  candidates: `savings.py:dashboard`, `year_end_summary_service.py` 2248 LOC,
  `carry_forward_service.py`, `savings_dashboard_service.py`,
  `dashboard_service.py`) and classify mixed concerns (HTTP / business / data
  access in one body). For OCP, Explore sweep `grep -rn` for
  `AccountType\.\|acct_type.*name\|account_type.*name\|\.name ==\|in (.*ENUM`
  and any hardcoded type-ID frozenset (`TRADITIONAL_TYPE_ENUMS` and siblings);
  classify each as enum/string/type-ID dispatch vs metadata-flag
  (`has_amortization` / `has_interest` / `is_pretax` / `is_liquid` /
  `has_parameters`). For ISP, grep service signatures for opaque bags
  (`ctx`, `base_args`, `**kwargs`, large dataclasses passed whole) and record
  the used-field / total-field ratio at each call. For DIP, identify services
  taking concrete model classes where a plain-data DTO is the established
  pattern (`PaymentRecord` is the cited positive control -- find the
  negatives). For LSP, find multi-account-type calculation services that branch
  on subtype rather than a common interface.

Stop condition: the `S6-` series written with, per finding, the live metric
(line count / branch construct / field-usage ratio) proving the violation and
the explicit statement of whether the roadmap's "addressed" claim holds at
current source; session ends.

### P6-c -- Boundary: layering + Transfer Invariant 5

Goal: prove the Routes->Services->Models boundary by grep, and re-prove (not
inherit from Phase 3 F-012) the two transfer-invariant structural boundaries.

Required reading (bounded):
- `financial_calculation_audit_plan.md:670-676` (6.3).
- `CLAUDE.md` Transfer Invariants section (lines 132-141) and the
  Routes->Services->Models architecture statement (lines 95-101).
- `00_priors.md:330-351` (E-05..E-09, the transfer-invariant expectations);
  `03_consistency.md:823-862` (F-012 shadow / Invariant 5) -- re-opened, not
  quoted.
- Live source via Explore sweep: `grep -rn 'from flask import\|import flask\|
  request\.\|session\[\|current_app\|g\.' app/services/` (every hit is a
  candidate boundary violation -- classify each as a real Flask-object
  dependency vs an incidental name collision by Reading the line). Then
  `grep -rn 'budget\.transfers\|Transfer(' app/services/ app/routes/` and
  classify every read/write of the `transfers` table as legitimate (transfer
  service CRUD / recurrence template management) or a violation (any non-
  transfer-service balance-path read; any non-transfer-service shadow
  mutation). Prove the *absence* of a second shadow writer and a second
  `budget.transfers` balance reader with the grep that returns empty, pasted.

Stop condition: the `B6-` series written -- every `app/services/` Flask-object
import classified, every `budget.transfers` touch classified, the
absence-proof greps pasted; session ends.

### P6-d -- Verification and consolidation gate (trust-but-verify capstone)

No new structural analysis. Verify and consolidate.

Tasks:
1. **Spot-check:** choose >= 15 cited sites at random across the `D6-`/`S6-`/
   `B6-` findings; re-resolve each to live source (grep/Read); show the table
   and the pass count. Threshold 100%; any miss reopens that finding's session
   before the gate can pass.
2. **Tag-completeness reconciliation:** confirm every `Phase-6` / `DRY note` /
   `SRP note` tag in `03_consistency.md` and `04_source_of_truth.md` maps to a
   `06_dry_solid.md` finding (or is explicitly recorded as superseded/no-longer-
   resolving with the divergence noted). Confirm the section 1 register is
   fully consumed. Confirm at least the carried untagged candidates
   (effective_amount mirrors beyond S10/S14/T1/T4, the full `quantize`
   register) were swept, not sampled.
3. **E-NN consistency roll-up:** per `D6-` finding, confirm the recommended
   single source of truth is consistent with its governing E-NN and does not
   invent a target no E-NN states. Confirm the E-26 `ROUND_CEILING` and E-28
   anchor-CHECK sanctioned exceptions were excluded as findings.
4. **Inherited-vs-independent roll-up:** per finding, confirm/narrow/contradict
   vs the section 1 register and the Phase-3 tags; any contradiction is a Phase
   6 finding against the prior phase with both citations.
5. **Acceptance gate** (section 5), each criterion with evidence/verdict.
6. **Handoff:** what Phase 7 (test gaps -- each `D6-` consolidation implies a
   cross-site equivalence test), Phase 8 (findings -- `D6-`/`S6-`/`B6-` feed
   the structural findings with severity assigned there, not here), Phase 9
   (open questions -- any genuinely new ambiguity, else "none, mirrors Phase 5
   G8") inherit. Carry the A-26 `estimated_retirement_tax_rate` NULL-semantics
   tail forward unchanged (still out of structural scope; recorded, not
   dropped).
7. `git status` pasted, showing only `docs/audits/financial_calculations/`.

Stop condition: gate section appended to `06_dry_solid.md`; "Phase 6 complete"
recorded with the gate roll-up, or, if any gate fails, the failing criterion
and the session to reopen named, and the session stops without declaring
completion.

## 5. Phase 6 acceptance gate (mirrors Phase 5 section 5)

Phase 6 is complete only when all hold, each with shown evidence:

- **G1** `06_dry_solid.md` exists, non-empty; three parts (DRY 6.1, SOLID 6.2,
  boundary 6.3), every finding carrying every section-3 element.
- **G2** Every duplication/violation site cites `file:line` Read or grepped
  during a Phase 6 session; no site sourced only from a Phase-3/4 tag without
  re-resolution.
- **G3** Every DRY finding shows the expanded-form comparison (the actual
  duplicated lines, not just call sites); every "N places" claim has N
  citations or is marked incomplete.
- **G4** Every SRP/OCP finding shows the live metric (current line count /
  current branch construct) and an explicit verdict on whether the roadmap's
  "addressed"/`complete-per-plan` claim holds at current source -- proven by
  grep, not trusted.
- **G5** Spot-check >= 15 sites, 100% resolve; table and count shown.
- **G6** Every `Phase-6` / `DRY note` / `SRP note` tag in `03`/`04` maps to a
  finding or is recorded as superseded with the divergence noted; the section 1
  register is fully consumed; the mandated sweeps were swept, not sampled.
- **G7** Each `D6-` finding's recommended single source of truth is consistent
  with its governing E-NN; the E-26 `ROUND_CEILING` and E-28 anchor-CHECK
  sanctioned exceptions are excluded as findings; no fix diff was produced
  (or, if one was, it was reverted and recorded as a remediation-direction
  sentence per audit-plan 10.6).
- **G8** No new auditor-invented "obvious" single-source expectation added to
  `09_open_questions.md`; only genuinely new ambiguities, if any.
- **G9** `git status` shows only `docs/audits/financial_calculations/` files
  changed; no source, test, migration, template, or JS file touched.

## 6. Anti-shortcut prompt (paste at the top of every Phase 6 session)

> This session is part of a read-only audit running in Claude Code's `plan`
> permission mode. Document findings in `06_dry_solid.md` with file and line
> citations to the actual source, Read or grepped this session. Read the
> relevant function fully before drawing conclusions about its behavior. Verify
> every "computed in N places" claim by running `grep`/`glob` and pasting all N
> hits; expand helpers inline and show both forms before claiming they
> duplicate. Prove every SRP/OCP claim from the live file's current line count
> and current branch structure -- the roadmap's "addressed" is not evidence.
> Treat the Phase-3/4 `Phase-6` tag register as a hypothesis to falsify, not a
> checklist: re-open each tag at live source and also find the duplications
> Phase 3 did not tag. Consume the locked E-18..E-28 intent; do not invent a
> new single-source target no E-NN states, and honor the E-26 `ROUND_CEILING`
> and E-28 anchor-CHECK sanctioned exceptions. Recommend the single source of
> truth in the report only -- never write a fix; a diff is a finding artifact
> to revert. Use the Explore subagent for the repository-wide sweeps so raw
> file contents stay out of this session. Findings go into `06_dry_solid.md`;
> source files, tests, and migrations remain untouched. Stay within the
> assigned session's concern scope.

## 7. Failure modes and remedies

- Kitchen-sink session -> one concern-family per session; `/clear` between.
- Infinite exploration -> the bounded reading lists in section 4 and the
  mandatory Explore sweeps; do not widen without recording why.
- Trust-then-verify gap -> section 2 contract; the P6-d spot-check is mandatory
  and the Phase-3/4 tags are re-opened, not quoted.
- Roadmap trust -> SRP/OCP proven by grep at current source; "the roadmap says
  addressed" is a prompt to grep, not a verdict.
- Refactor temptation -> Phase 6 recommends in prose only; a diff is reverted
  and recorded as a remediation-direction sentence (audit-plan 10.6/10.7).
- Scope drift -> if a session starts proposing fixes or touching non-financial
  structure, stop; the deliverable is the map, not the patch.

## 8. Ready-to-paste session prompts

Run strictly in order (P6-a1 -> P6-a2 -> P6-b -> P6-c -> P6-d), each in its own
session started with `claude --permission-mode plan`, with `/clear` between. Do
not use `@` on the large audit docs (`00`/`01`/`02`/`03`/`04`/`09`) -- `@`
reads the whole file and blows context; the prompts instruct ranged Reads and
greps instead. Each session's recovery state is the accumulated
`06_dry_solid.md` (audit-plan 10.5).

### Prompt P6-a1 (DRY: canonical-producer-absence family)

```text
This session is part of a read-only audit running in Claude Code's `plan`
permission mode. Document findings in
docs/audits/financial_calculations/06_dry_solid.md with file and line
citations to the actual source, Read or grepped this session. Read the
relevant function fully before drawing conclusions. Verify every "computed in
N places" claim by running grep/glob and pasting all N hits; expand helpers
inline and show both forms before claiming they duplicate. Treat the
Phase-3/4 `Phase-6` tag register as a hypothesis to falsify: re-open each tag
at live source and also find duplications Phase 3 did not tag. Consume the
locked E-18..E-27 intent; do not invent a single-source target no E-NN states.
Recommend the single source of truth in the report only -- never write a fix.
Findings go into 06_dry_solid.md; source/tests/migrations untouched.

This is Phase 6 session P6-a1. Follow phase6_plan.md section 4 (P6-a1) and the
trust-but-verify contract in section 2. Scope: the DRY findings for the
canonical-producer-absence family only -- the figures E-18 (loan resolver),
E-19 (anchor resolver), E-24 (loan-obligation aggregator + 26/12 factor), E-25
(period subtotal), E-27 (balance-as-of-date), plus the F-010 _sum_remaining /
_sum_all split.

Bounded reading (Read exactly these ranges, in full, before concluding; do not
widen without recording why):
- docs/audits/financial_calculations/financial_calculation_audit_plan.md lines
  614-643.
- docs/audits/financial_calculations/00_priors.md lines 184-213 (E-18, E-19)
  and 260-311 (E-24, E-25, E-27).
- docs/audits/financial_calculations/05_symptoms.md lines 1701-1740 (Phase 5
  handoff).
- docs/audits/financial_calculations/03_consistency.md lines 109-213 (F-001),
  283-340 (F-003), 580-648 (F-008), 649-726 (F-009), 727-768 (F-010),
  1009-1149 (F-013). Then grep 03_consistency.md and 04_source_of_truth.md for
  "Phase-6", "DRY note", "SRP note"; enumerate every hit's line and Read only
  the surrounding finding for each (not the whole file). Also Read
  04_source_of_truth.md lines 2129-2146 (drift register).
- Live app/ source, Read in full at the relevant functions: grep app/ for
  "calculate_monthly_payment(" to get the exact call set, then Read each
  call-site function in full (amortization_engine.py, loan.py,
  debt_strategy.py, savings_dashboard_service.py, year_end_summary_service.py,
  dashboard_service.py as they appear); balance_calculator.py _sum_remaining /
  _sum_all / _entry_aware_amount; the grid subtotal site (grep grid.py for
  "subtotal"); calendar_service.py month-end path; the 26/12 factor sites (grep
  app/ for "26 / 12", 'Decimal("26")', "PAY_PERIODS_PER_YEAR"); the dual
  per-account dispatcher near savings_dashboard_service.py:294 and
  year_end_summary_service.py:2036.

Produce in 06_dry_solid.md a "Part 6.1 DRY -- canonical-producer-absence
family" section with one D6-NN finding per root (loan resolver, anchor
resolver, period subtotal, balance-as-of-date, obligation aggregator + 26/12
factor, the _sum_* split), each carrying every element of phase6_plan.md
section 3: principle + citation, every grepped site, the expanded-form
comparison, governing E-NN, recommended single source of truth (report only),
inherited-vs-independent note, blast radius cross-linked to the Phase 3/4/5
finding that observed the drift. Do not run the app. Do not modify code. End by
writing the section and stopping; paste `git status` confirming only
docs/audits/financial_calculations/ changed.
```

### Prompt P6-a2 (DRY: cross-cutting micro-duplications)

```text
[Same anti-shortcut preamble as P6-a1, first paragraph verbatim.]

This is Phase 6 session P6-a2. Follow phase6_plan.md section 4 (P6-a2) and the
trust-but-verify contract in section 2. Scope: the high-count cross-cutting DRY
sweeps only -- E-26 (rounding-helper absence: the TWO_PLACES constant
redeclaration and the monetary quantize sites, classifying which are banker's
rounding), the effective_amount mirror inventory, the inline status-filter
inventory, and the PA-05 magic-number-fallback inventory. Use the Explore
subagent for each repository-wide sweep (one Explore invocation per pattern,
thoroughness very thorough) so raw file contents stay out of this session;
aggregate the returned file:line registers here.

Bounded reading (Read exactly these ranges before concluding):
- docs/audits/financial_calculations/financial_calculation_audit_plan.md lines
  624-643 (6.1; the inline-duplication bullets at 637-639 specifically).
- docs/audits/financial_calculations/00_priors.md lines 286-296 (E-26), 814
  (PA-05), 352-385 (E-10..E-17).
- docs/audits/financial_calculations/03_consistency.md lines 2110-2267 (P3-c
  effective_amount sweep, S10/S14/T1/T4 mirror table). Grep 03_consistency.md
  and 02_concepts.md for "effective_amount" and enumerate the consumer
  register; Read only the bounded finding around each hit.
- Grep 01_inventory.md for "quantize", "TWO_PLACES", "ROUND_HALF",
  "effective_amount", "actual_amount", "estimated_amount" to seed the sweep
  target list; do not read it whole.
- Live source via Explore sweeps (one per pattern): grep -rn 'quantize' app/;
  grep -rn 'TWO_PLACES\|Decimal("0.01")' app/; grep -rn 'ROUND_HALF\|ROUND_CEIL'
  app/; grep -rn 'actual_amount if .*else .*estimated_amount' app/ (plus the
  Jinja and JS equivalents under app/templates/ and app/static/js/);
  grep -rn 'status_id != \|status_id == \|projected' app/ scoped to balance/
  subtotal paths; grep -rn '0\.04\|4\.0\|7\.0' app/ scoped to retirement/growth
  fallback sites. The main session Reads the live lines to classify each
  quantize site as monetary-boundary vs intermediate and to exclude the
  sanctioned savings_goal ROUND_CEILING site (E-26 says it is NOT a finding).

Produce in 06_dry_solid.md a "Part 6.1 DRY -- cross-cutting micro-duplications"
section: D6-NN findings for the rounding-helper absence (with the full
TWO_PLACES file register and the classified quantize register, banker's-
rounding sites called out, the ROUND_CEILING exception explicitly excluded),
the effective_amount mirror inventory, the inline-status-filter inventory, the
PA-05 magic-number inventory. Every finding carries every section-3 element
with full grepped registers. Do not run the app. Do not modify code. End by
writing the section and stopping; paste `git status`.
```

### Prompt P6-b (SOLID)

```text
[Same anti-shortcut preamble as P6-a1, first paragraph verbatim, plus:] Prove
every SRP/OCP claim from the live file's current line count and current branch
structure -- the roadmap's "addressed" is not evidence.

This is Phase 6 session P6-b. Follow phase6_plan.md section 4 (P6-b) and the
trust-but-verify contract in section 2. Scope: SOLID only -- SRP, OCP, LSP,
ISP, DIP across app/services/ and app/routes/.

Bounded reading (Read exactly these ranges before concluding):
- docs/audits/financial_calculations/financial_calculation_audit_plan.md lines
  644-669 (6.2).
- docs/audits/financial_calculations/00_priors.md lines 122-149 (standards
  thresholds) and 386-452 (watchlist W-001..W-052; note planned-per-plan vs
  complete-per-plan dispatch rows).
- Grep 01_inventory.md for the per-file function lists as needed; do not read
  whole.
- Live source: run `wc -l app/services/*.py app/routes/*.py`; Read in full
  every function over 200 lines (start with savings.py:dashboard,
  year_end_summary_service.py, carry_forward_service.py,
  savings_dashboard_service.py, dashboard_service.py) and classify mixed
  concerns. For OCP use an Explore sweep: grep -rn for AccountType / acct_type
  name comparisons, ".name ==", hardcoded type-ID frozensets
  (TRADITIONAL_TYPE_ENUMS and siblings); classify each as enum/string/type-ID
  dispatch vs metadata flag (has_amortization/has_interest/is_pretax/is_liquid/
  has_parameters). For ISP grep service signatures for opaque bags (ctx,
  base_args, large dataclasses passed whole) and record used/total field
  ratios. For DIP find services taking concrete model classes where a DTO is
  the pattern (PaymentRecord is the positive control). For LSP find
  multi-account-type calc services branching on subtype.

Produce in 06_dry_solid.md a "Part 6.2 SOLID" section: S6-NN findings, each
with the live metric (current line count / branch construct / field-usage
ratio) and an explicit verdict on whether the roadmap's addressed /
complete-per-plan claim holds at current source. Do not run the app. Do not
modify code. End by writing the section and stopping; paste `git status`.
```

### Prompt P6-c (boundary)

```text
[Same anti-shortcut preamble as P6-a1, first paragraph verbatim.]

This is Phase 6 session P6-c. Follow phase6_plan.md section 4 (P6-c) and the
trust-but-verify contract in section 2. Scope: boundary violations only --
the Routes->Services->Models layering and the two Transfer-Invariant-5
structural boundaries, re-proven from source (not inherited from Phase 3
F-012).

Bounded reading (Read exactly these ranges before concluding):
- docs/audits/financial_calculations/financial_calculation_audit_plan.md lines
  670-676 (6.3).
- CLAUDE.md lines 95-101 (architecture) and 132-141 (Transfer Invariants).
- docs/audits/financial_calculations/00_priors.md lines 330-351 (E-05..E-09).
- docs/audits/financial_calculations/03_consistency.md lines 823-862 (F-012);
  re-open at live source, do not quote.
- Live source via Explore sweep: grep -rn 'from flask import\|import flask\|
  request\.\|session\[\|current_app\|g\.' app/services/ -- Read each hit's line
  and classify as a real Flask-object dependency vs incidental name collision.
  Then grep -rn 'budget\.transfers\|Transfer(' app/services/ app/routes/ and
  classify every transfers-table touch as legitimate (transfer service CRUD /
  recurrence template management) or a violation. Paste the empty-result greps
  that prove the absence of a second shadow writer and a second
  budget.transfers balance reader.

Produce in 06_dry_solid.md a "Part 6.3 Boundary" section: B6-NN findings,
every app/services/ Flask-object import classified, every budget.transfers
touch classified, the absence-proof greps pasted. Do not run the app. Do not
modify code. End by writing the section and stopping; paste `git status`.
```

### Prompt P6-d (verification and consolidation gate)

```text
This session is part of a read-only audit running in Claude Code's `plan`
permission mode. Verify every factual claim by re-resolving the cited
file:line to live source; do not recall from memory and do not trust a prior
session's citation without re-opening it. No new structural analysis. The gate
goes into docs/audits/financial_calculations/06_dry_solid.md; source/tests/
migrations untouched.

This is Phase 6 session P6-d, the trust-but-verify capstone. Follow
phase6_plan.md section 4 (P6-d) and the acceptance gate in section 5. Read the
full docs/audits/financial_calculations/06_dry_solid.md, phase6_plan.md
sections 1-5, and re-grep 03_consistency.md and 04_source_of_truth.md for
"Phase-6"/"DRY note"/"SRP note" for the tag-completeness reconciliation.

Do exactly these tasks, appending to 06_dry_solid.md:
1. Spot-check >= 15 cited sites at random across the D6-/S6-/B6- findings;
   re-resolve each to live source; show the table and pass count. Threshold
   100%; any miss reopens that session before the gate passes -- record and
   stop.
2. Tag-completeness reconciliation: every Phase-6/DRY note/SRP note tag in
   03/04 maps to a finding or is recorded superseded with the divergence;
   phase6_plan.md section 1 register fully consumed; mandated sweeps swept not
   sampled.
3. E-NN consistency roll-up: each D6- recommended single source consistent
   with its governing E-NN; the E-26 ROUND_CEILING and E-28 anchor-CHECK
   sanctioned exceptions excluded as findings.
4. Inherited-vs-independent roll-up: per finding confirm/narrow/contradict vs
   section 1 register and Phase-3 tags; contradictions are Phase 6 findings
   against the prior phase with both citations.
5. Acceptance gate G1-G9 from phase6_plan.md section 5, each with evidence/
   verdict.
6. Handoff to Phase 7/8/9; carry the A-26 estimated_retirement_tax_rate
   NULL-semantics tail forward unchanged (recorded, not dropped).
7. Paste `git status`, confirming only docs/audits/financial_calculations/
   changed.

End by recording "Phase 6 complete" with the G1-G9 roll-up, or, if any gate
fails, name the failing criterion and the session to reopen and stop without
declaring completion.
```
