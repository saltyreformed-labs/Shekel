> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Financial Calculation Audit Plan

## 0. Purpose and ground rules

This plan instructs Claude Code to perform a comprehensive, read-only audit of every financial
calculation in the Shekel codebase. The goal is not to fix the symptoms reported by the developer.
The goal is to produce a structural map of the calculation surface, prove (or disprove) that every
domain concept is computed consistently across pages and services, and report all divergences with
file/line evidence so the developer can decide what to do next.

### Hard rules for this audit

1. **No code changes.** This is a read-only audit. Do not edit, refactor,
   reformat, or "clean up" any file. If you are tempted to fix something,
   write it down in the findings report instead. The deliverable is
   evidence and reasoning, not a patch.

2. **No test changes.** Do not add, modify, or delete tests. If you want
   to verify a hypothesis with a test, write it as a proposed test in the
   findings report and stop.

3. **Read the entire file before drawing conclusions.** Do not infer
   behavior from a function name, a docstring, or a 30-line excerpt. The
   bugs the developer is chasing live in the parts of the code that look
   harmless on first glance.

4. **Evidence over opinion.** Every claim in every report file must cite
   `path/to/file.py:line` or quote the relevant SQL. If you cannot cite
   it, you cannot claim it.

5. **No guessing about financial logic.** When the intended behavior of a
   calculation is unclear, write the question in the findings report and
   stop on that item. Do not pick the interpretation that "looks right."
   The developer will answer the question in a follow-up session.

6. **Stay in scope.** The scope is calculations of money, balances,
   payments, contributions, interest, growth, projections, principal,
   payoff dates, taxes, deductions, paychecks, debt summaries, savings
   goals, emergency-fund metrics, and any aggregate or derived figure
   displayed in a page or chart. Routing, auth, templates rendering
   non-financial UI, and migrations not touching numeric columns are
   out of scope.

7. **Do not commit anything.** All audit artifacts are written to
   `docs/audit/financial_calculations/` as new files. Do not amend
   existing roadmap, plan, or audit files.

### What to read and how

Phase 0 has two purposes. First, orient on what the project is supposed to be. Second, capture a
small set of testable behavioral claims that the audit will verify against the code. Phase 0 is
intentionally lightweight. The audit's center of gravity is reading the actual code in Phases 1
through 3; documents are reference material, not the deliverable.

The `docs/` directory contains roughly 75 files accumulated over the life of the project. Many are
defunct (superseded roadmaps, completed plans, status notes that no longer apply) and many are
unrelated to financial calculations (deployment runbooks, environment setup, non-financial feature
designs). Reading all of them is the wrong default. Phase 0 is structured to triage this directory
before any deep reading begins.

The output of Phase 0 is a priors document of roughly 200-500 lines. If it is longer than that, the
auditor is summarizing instead of extracting. Stop and rewrite tighter.

### 0.1 Triage of /docs

List the contents of `docs/`. For each file, decide based on filename whether it is worth opening at
all. Then for the remainder, open the first 50 lines (or the table of contents if one is present)
and classify each file as one of:

- **RELEVANT_CURRENT.** Describes the current intended behavior of the
  code in an area touching financial calculations. Read in full in
  later sub-phases.
- **RELEVANT_HISTORICAL.** Describes a past intended behavior in a
  financial-calculation area, but a later document or implementation
  has superseded it. Consult only if the code's intent is unclear.
- **IRRELEVANT.** Does not touch financial calculations. Skip.
- **DEFUNCT.** Explicitly retired, marked WIP and abandoned, or
  self-describes as superseded. Skip.

Filename heuristics that resolve classification without opening the file:

- A file with a version suffix (e.g., `project_roadmap_v3.md`,
  `project_roadmap_v4-2.md`, `project_roadmap_v4-4.md`) is superseded
  if a newer version of the same family exists. Only the newest is
  RELEVANT_CURRENT; the rest are RELEVANT_HISTORICAL or DEFUNCT.
- Filenames containing "runbook", "deployment", "environment_setup",
  "docker", "nginx", "cloudflared", or "secrets" are typically
  IRRELEVANT for this audit.
- Filenames with "implementation_plan", "design", "addendum",
  "rework", "refactor", "remediation", "audit" are candidates for
  RELEVANT_*; they require opening to classify.
- A file the current roadmap describes as "stale" or "predates X" is
  RELEVANT_HISTORICAL at most.
- A file whose work the current roadmap describes as COMPLETE is
  RELEVANT_HISTORICAL; the audit cares about what the code does now,
  not what the plan said it should do.

Output: a triage table at the top of the priors document, one row per file, with columns: filename,
classification, one-sentence reason. The triage is the audit's reading plan for Phase 0. The
developer should review the table and correct misclassifications before deeper reading begins.

### 0.2 Read the standards and the current roadmap

Read these in full. They define what the project requires of itself.

- `CLAUDE.md`, including the Transfer Invariants section.
- `docs/coding-standards.md`.
- `docs/testing-standards.md`.
- The current roadmap, identified in the triage. Likely
  `docs/project_roadmap_v4-6.md` or whichever later version exists.

Capture in the priors document:

- The status workflow values and their meanings (projected, done,
  received, settled, credit, cancelled).
- The transfer invariants, copied verbatim from CLAUDE.md.
- Any rule the standards documents impose on calculation code.
- The roadmap's current statement of what is done, in progress, and
  pending in financial-calculation areas. One paragraph total. Do not
  summarize the roadmap section by section.

### 0.3 Behavioral expectations from the developer

These are invariants the developer has stated about what the code must do. The audit verifies each
one against the actual code in Phases 3 and 5. Failure to satisfy any of them is a finding,
regardless of whether a plan or roadmap documents the expectation. This list is the most important
input to the rest of the audit; the plans are not.

Initial expectations from the developer:

1. **Transfer to a debt account splits two ways.** A transfer out of
   a checking account and into a mortgage (or any debt account)
   reduces the checking balance by the full payment amount, including
   principal, interest, escrow, and any other components. The
   destination debt account records the full amount as received but
   only the principal portion of the payment reduces the loan
   principal balance. Interest, escrow, and any other non-principal
   components are recorded as part of the transfer but do not reduce
   loan principal.
2. **Monthly payment is stable inside the fixed-rate window of an
   ARM.** A 5/5 ARM during the first five years has one monthly
   payment value. The amortization engine, however many times it is
   called and from whichever entry point, produces the same payment
   for any month in that window. Fluctuation by even a few cents is
   a finding.
3. **Loan principal updates as confirmed transfers occur.** When a
   transfer to a debt account is settled, the real loan principal
   reflects the principal portion of that payment. Whether the update
   happens by writing a stored column or by recomputing from
   confirmed payments is an implementation choice; the audit must
   determine which approach the code uses, whether it is consistent
   across display sites, and whether it is correct.
4. **An account balance is the same number on every page that shows
   it.** The checking-account balance for the current pay period on
   the grid, on `/savings`, and on `/accounts` is the same number for
   the same inputs. If two views are intentionally different (one
   shows the floor, another shows the projection), the difference is
   labeled and documented; an unlabeled difference is a finding.

The audit grows this list during Phase 1 (inventory) by extracting invariants from function
docstrings, the CLAUDE.md transfer invariants, and any in-code comments stating "must" or "always"
or "never". Each addition cites its source.

When the audit identifies a candidate invariant that no source explicitly states (the auditor
concluded "obviously the code should do X"), do not silently treat it as verified expectation. Add
it to `09_open_questions.md` for the developer to confirm or correct. "Obvious" expectations are
exactly what produced the silent drift the audit is hunting; the audit does not get to invent new
ones without confirmation.

### 0.4 Plans, skim for behavioral claims only

The implementation plans below are the priority set the developer flagged. Do not read them
exhaustively. The audit is not summarizing plans; it is extracting testable claims for Phase 3 to
verify against the code.

Read each plan in skim mode with one focused goal: extract claims of the form "after this work, X
should happen" or "the code must do Y" that touch financial calculations. Skip everything else:
prose, UX mockups, migration sequences, rollout notes, tradeoff discussions. A 3000-line plan should
produce 15-30 watchlist entries, not a 3000-line summary.

Priority plans:

- `docs/implementation_plan_account_parameters.md`
- `docs/carry-forward-aftermath-implementation-plan.md`
- `docs/implementation_plan_section5.md`
- `docs/transfer_rework_implementation.md`
- `docs/implementation_plan_envelope_view.md`
- `docs/implementation_plan_arm_anchor_refactor.md`

If 0.1 surfaced a RELEVANT_CURRENT plan not on this list, add it to the skim set.
RELEVANT_HISTORICAL plans are skimmed only for claims that the current roadmap or a more recent plan
does not address; for the rest, the more recent document supersedes them. DEFUNCT and IRRELEVANT
plans are not opened.

For each watchlist entry, capture:

- The behavioral claim, in one sentence.
- The plan and section that stated it.
- Whether a more recent plan or the roadmap modifies or supersedes
  the claim. If so, record the modification.

When two plans state contradicting claims for the same concept, record both with citations and add
an entry to `09_open_questions.md` asking the developer which is current.

The plan-vs-code watchlist is the only output Phase 3 needs from plan reading. If a watchlist entry
would reproduce something already in the developer-stated expectations from 0.3, do not duplicate
it; note the cross-reference instead.

### 0.5 Prior audits

For audit-flavored documents classified RELEVANT_CURRENT in 0.1, read in full and capture the open
findings. For documents classified RELEVANT_HISTORICAL, capture only findings that the roadmap does
not describe as remediated; skip the rest. The audit is not re-doing prior audits; it is building on
them.

### 0.6 Patches and recent commits

- Any `*.patch` file in the working tree, for example
  `amortization-fix.patch`. Record what it changed, what behavior
  the change was intended to produce, and whether the patch is
  currently applied to the code or sits unmerged.
- A windowed `git log` pass over the past six months for commit
  messages mentioning calculation, balance, principal, payment,
  anchor, ARM, drift, or fix. Capture the commit hash and a
  one-sentence note. Do not run `git log` exhaustively.

### Phase 0 deliverable

The priors document contains, in this order:

1. Triage table for `docs/`.
2. Standards and roadmap summary.
3. Developer-stated behavioral expectations, with any additions
   discovered while reading the standards.
4. Plan-vs-code watchlist (extracted claims, no plan summaries).
5. Cross-plan contradictions and any open questions about which is
   current.
6. Open prior-audit findings still in scope.
7. Patches and recent commits.

Target length: 200-500 lines. If longer, the auditor is summarizing instead of extracting; stop and
rewrite tighter. The deliverable is not a re-presentation of the project's history; it is the
smallest set of testable claims the rest of the audit needs.

## 1. Phase 1: Inventory the calculation surface

The objective of this phase is to enumerate, with no judgment, every location in the codebase that
produces or consumes a financial figure. Output:
`docs/audit/financial_calculations/01_inventory.md`.

**Run this phase using Claude Code's built-in Explore subagent, one invocation per layer.** Section
10.1c explains the rationale and gives the prompt pattern. Doing the inventory directly from the
main session fills the context with raw file content before any structured output is written; using
Explore keeps the verbose reads in the subagent and returns only summaries to the main session.

### 1.1 Service layer

For every file under `app/services/`, record:

- File path and total line count.
- Every public function or class method, with its signature, return
  type, and a one-sentence description of what it returns. Do not
  paraphrase the docstring; describe what the code actually does.
- For each function, the financial concepts it produces. Use a short
  controlled vocabulary: `checking_balance`, `account_balance`,
  `projected_end_balance`, `loan_principal_real`, `loan_principal_stored`,
  `monthly_payment`, `principal_paid_per_period`, `interest_paid_per_period`,
  `payoff_date`, `total_interest`, `apy_interest`, `growth`,
  `employer_contribution`, `contribution_remaining_limit`,
  `paycheck_gross`, `paycheck_net`, `taxable_income`, `federal_tax`,
  `state_tax`, `fica`, `pre_tax_deduction`, `post_tax_deduction`,
  `transfer_amount`, `effective_amount`, `goal_progress`,
  `emergency_fund_coverage_months`, `dti_ratio`, `net_worth`,
  `savings_total`, `debt_total`, `period_subtotal`,
  `chart_balance_series`, `year_summary_*`, and so on. Add new tokens
  to this vocabulary if the code requires them; record the additions
  at the top of the inventory.
- The status filters used (which `Status` values are included or
  excluded). Cite the line.
- The transaction-type filters used (income / expense / transfer
  shadows). Cite the line.
- The period scope (anchor period, current period, list of periods,
  forward range, full year). Cite the line.
- Whether the function uses `effective_amount`, `estimated_amount`,
  `actual_amount`, or `amount` directly. Cite the line.
- Quantization or rounding calls, with the precision and rounding mode
  (`Decimal("0.01")`, `ROUND_HALF_UP`, etc.).
- All calls this function makes to other service functions or to
  database models.

Do not infer anything in this phase. Only record. Even functions that appear to be helpers or pure
formatters are recorded.

### 1.2 Route layer

For every file under `app/routes/`, record every route handler that prepares a financial figure for
rendering. For each one:

- Path, HTTP method, view function name.
- What it queries from the database (tables, joins, filters).
- Which service functions it calls and what arguments.
- What it places in the template context. Specifically: the variable
  name, the type, and which financial concept it represents. Use the
  same controlled vocabulary as section 1.1.
- The template it renders.

### 1.3 Template layer

For every template under `app/templates/` that renders a financial figure, record the variables it
consumes from the context, the format applied (filters like `format_currency`, `"{:,.2f}".format`),
and any arithmetic done in Jinja itself. Arithmetic in Jinja is a finding; flag it but do not fix
it.

### 1.4 Static / JavaScript layer

For every JS file under `app/static/js/` that does numeric work (charts, grid math, balance
recomputation client-side), record:

- The file and the function.
- What numeric inputs it consumes (data attributes, JSON payload, form
  fields).
- What it computes and displays.
- Whether the same concept is also computed on the server.

Any case where the same domain concept is computed both client-side and server-side is flagged in
the inventory. Do not assume one is right. Just record both.

### 1.5 Models and computed properties

For every SQLAlchemy model under `app/models/`, record:

- Numeric columns: name, type (Numeric precision/scale, Integer,
  etc.), nullability, server defaults, CHECK constraints.
- Computed properties (`@property`, `@hybrid_property`) that return a
  numeric value. For each: name, the formula it implements, what it
  reads from, and which other code calls it.

Pay particular attention to properties named `effective_amount`, `is_income`, `is_expense`,
`is_settled`, and any property on `Transaction`, `Account`, `LoanParams`, `InterestParams`,
`InvestmentParams`, `SalaryProfile`, `Deduction`, `Transfer`, and `PayPeriod`.

### 1.6 Database queries that aggregate money

Grep the entire codebase for SQL aggregates that operate on numeric columns: `func.sum`, `func.avg`,
`func.min`, `func.max`, `func.count`, raw SQL with `SUM(`, `AVG(`, etc. Record each occurrence with
file/line, the column being aggregated, the joins, and the filters. Aggregates over money outside of
services or in raw SQL are suspect and must be listed.

### 1.7 Inventory deliverable

The inventory document is a structured list, not prose. Use tables where they fit. The reader must
be able to grep the inventory by concept name and find every location that produces or consumes that
concept. If the inventory is hard to grep, restructure it.

Stop after Phase 1. Do not begin Phase 2 in the same session. The inventory is itself the input to
Phase 2; the developer should be able to skim it and spot obvious issues before the next session
begins.

## 2. Phase 2: Concept catalog

Output: `docs/audit/financial_calculations/02_concepts.md`.

For each concept in the controlled vocabulary that appeared at least once in Phase 1, produce a
section with:

- The concept's intended definition in one paragraph. Pull this from
  docstrings, the roadmap, and any prior audit document. If multiple
  definitions exist, list them all with citations and flag the
  discrepancy.
- The list of every implementation site (function or query) that
  produces a value for this concept, copied from the inventory.
- The list of every consumer site (route, template, JS, other
  service) that reads or displays a value for this concept.
- A "primary path" identification: which implementation is meant to
  be the canonical source. If the codebase does not make this clear,
  write "unknown" and add the question to the open questions list.

The concept catalog is the spine of every later phase. Do not move on until every concept used in
the app appears in this document.

## 3. Phase 3: Concept consistency audit

Output: `docs/audit/financial_calculations/03_consistency.md`.

For every concept in the catalog that has more than one implementation site or more than one
consumer that gets a value computed differently, perform the consistency audit below. This phase is
the core of the audit; the symptoms the developer is reporting are almost certainly manifestations
of failures here.

For each multi-path concept, produce a finding with the following schema:

```text
### Finding F-XX: <concept_name> consistency

- Concept: <token from controlled vocabulary>
- Paths compared: <path A>, <path B>, ...
- Path A: <file:line> -- <one-line description>
- Path B: <file:line> -- <one-line description>
- Compared dimensions:
    - Status filter: <set of StatusEnum values included>
    - Transaction-type filter: <income, expense, transfer-shadow inclusion rules>
    - Effective amount logic: <effective_amount, estimated_amount, actual_amount, raw amount>
    - Period scope: <which PayPeriod set>
    - Anchor handling: <how anchor balance / anchor period are treated>
    - Scenario filter: <scenario_id usage>
    - is_deleted handling: <inclusion rule>
    - Quantization: <Decimal precision and rounding mode>
    - Source-of-truth column read: <e.g., reads Account.current_anchor_balance directly vs computes>
- Divergences:
    - <bullet list of every place the paths disagree, with line citations>
- Risk:
    - <one paragraph: under what input conditions do the paths produce
      different numbers? Use a worked example with concrete inputs if
      it helps.>
- Verdict: AGREE | DIVERGE | UNKNOWN
- If DIVERGE: classification:
    - SILENT_DRIFT  -- the same concept is computed two ways and the
      values can disagree without raising an error.
    - DEFINITION_DRIFT -- the concept means subtly different things in
      different places (e.g., one excludes credit, one includes it).
    - SOURCE_DRIFT  -- one path reads a stored column, another computes
      it from primaries; the column can become stale.
    - ROUNDING_DRIFT -- the formulas agree algebraically but produce
      different results due to rounding order or precision.
    - SCOPE_DRIFT   -- the period set, scenario set, or filter set
      differs.
    - PLAN_DRIFT    -- the implementation deviates from what the
      relevant plan in the priors document declared. The plan may
      have been wrong; the code may have been wrong; the audit
      records the divergence with citations from both sides and
      lets the developer decide which side wins.
- Open questions for the developer: <list>
```

Compare every pair of paths for a concept, not just A-vs-B. With three implementations there are
three pairs and the audit must report all three.

When two paths look identical except for one filter, do not assume the filter difference is
intentional. Record it as a divergence and ask.

When a calculation is split across helpers (`_sum_remaining`, `_sum_all`, `_entry_aware_amount`,
`_resolve_best_estimate`, etc.), expand the helper inline mentally and compare the expanded forms.
Two helpers can have the same name and different bodies in different files; the comparison is on
behavior, not on call site.

### 3.1 Specific consistency comparisons that must be performed

The following comparisons are required regardless of whether the inventory surfaced them. They are
derived from the developer's reported symptoms and from the codebase architecture.

- `projected_end_balance` for the current pay period in the grid versus
  `checking_balance` shown on `/savings`. Trace both back to the
  service that computed them and prove or disprove that they are the
  same number for the same inputs. If they differ, classify the drift.
- `account_balance` shown on `/accounts` versus the value used by
  `savings_dashboard_service` versus the value at the current period
  in the grid. All three must be compared.
- `loan_principal_real` as derived from confirmed payments via the
  amortization engine versus `LoanParams.current_principal` as stored
  in the database versus the value displayed on `/accounts/<id>/loan`.
  Determine whether `current_principal` is updated when a transfer to
  the loan account is settled, and if so by which code path.
- `monthly_payment` as computed by the amortization engine on each
  call versus any stored or cached value. Specifically check whether
  the engine is invoked with different inputs (anchor balance, ARM
  rate selection, payment-record set) on different pages and whether
  those input differences explain the developer's observed
  fluctuation from $1911.54 to $1914.34 to $1912.94 to $1910.95.
- For any 5/5 ARM during its fixed-rate window, compute by hand from
  the loan parameters what the monthly payment should be and compare
  to every value the engine returns from every entry point. The
  payment must not change during the fixed-rate window. If the engine
  produces different payments on different calls during that window,
  the audit must explain why with code citations.
- `effective_amount` as defined on the `Transaction` model versus
  every direct read of `actual_amount` or `estimated_amount` in
  services, routes, templates, and JS. Every direct read is a
  potential bypass of the effective-amount rule and must be listed.
- The `_sum_remaining` versus `_sum_all` split in the balance
  calculator. These two helpers are called for the anchor period and
  for non-anchor periods respectively. Confirm by code reading that
  the only intended difference is the anchor-period semantics, and
  that no other behavioral difference (status filter, type filter,
  effective-amount logic) sneaks in.
- The handling of credit-status transactions everywhere. The grid
  excludes credit; the savings dashboard, the chart data service, and
  any per-account balance computation must do the same. Any divergence
  is a finding.
- Shadow transaction handling. Per the transfer invariants, the
  balance calculator queries only `budget.transactions` and never
  `budget.transfers`. Verify by grep that no service or route touches
  `budget.transfers` for balance computation. List every read of the
  `transfers` table and classify it as legitimate (CRUD, recurrence
  template management) or as a violation.
- Every entry on the plan-vs-code watchlist from the priors
  document. For each watchlist claim, locate the actual code that
  should embody the claim and compare. Each watchlist entry becomes
  a Phase 3 finding with a verdict: HOLDS (code matches the plan's
  intent), VIOLATED (code does something materially different), or
  PARTIALLY_HOLDS (code matches some but not all of the plan's
  declared outcomes). VIOLATED and PARTIALLY_HOLDS are both
  PLAN_DRIFT findings; partially-implemented plans are exactly the
  substrate that produces silent drift in the consuming pages.
  HOLDS findings are recorded so the developer can see at a glance
  which pieces of intent are still intact and which need re-work.
  When the watchlist entry came from a plan that another plan has
  superseded, note both plans in the finding and treat the current
  plan as the comparand. When two plans contradict each other and
  the code has to pick one, record which plan the code follows and
  flag the question for the developer.

Each of these required comparisons becomes its own finding in the consistency document, even if the
verdict is AGREE.

## 4. Phase 4: Source-of-truth and drift audit

Output: `docs/audit/financial_calculations/04_source_of_truth.md`.

Many of the symptoms the developer is reporting smell like stored columns drifting away from
computed values. This phase enumerates every stored numeric value that has, or should have, a
computational counterpart, and reports on the drift surface.

For each stored numeric column:

- Column: `<schema>.<table>.<column>`.
- What it represents.
- Computational counterpart: which service function computes the same
  concept from primaries, if any.
- Update path: which code paths write to the column. List every one.
- Read paths: which code paths read the column directly (without going
  through the computational counterpart). List every one.
- Drift risk: under what conditions can the stored value diverge from
  the computed value?
- Stale-detection: does the codebase have any check, warning, or test
  that catches stalenes? (Example: the `stale_anchor_warning` flag in
  the balance calculator. Does anything actually consume that flag?
  Cite the consumer or note its absence.)

Mandatory columns to investigate (not exhaustive):

- `Account.current_anchor_balance`
- `Account.current_anchor_period_id`
- `LoanParams.current_principal`
- `LoanParams.original_principal`
- `LoanParams.interest_rate`, especially for ARM accounts where rate
  history may be authoritative
- Any "balance" columns on InterestParams or InvestmentParams (e.g.,
  cached projected balances).
- Any ledger-style or audit columns that store dollar values.

For each, produce the finding above and classify the column as one of:

- AUTHORITATIVE -- the column is the source of truth and computed
  values derive from it.
- CACHED -- the column mirrors a computation that should be
  authoritative; staleness is possible.
- DERIVED -- the column is a denormalization of other fields and
  must be recomputed on any change.
- UNCLEAR -- the codebase does not consistently treat the column as
  any of the above.

UNCLEAR is a finding by itself.

## 5. Phase 5: Symptom-driven investigation

Output: `docs/audit/financial_calculations/05_symptoms.md`.

This phase investigates the specific symptoms the developer reported, using the inventory and
consistency findings as inputs. The point is not to fix anything; it is to explain the symptom in
terms of the structural findings above, so the developer knows whether the symptom is one bug or a
manifestation of several.

For each reported symptom, produce:

- Symptom description (from the developer's own words).
- Reproduction path: which page, which user input, which account.
- Hypothesis tree: starting from the displayed value, walk backward
  through every function call, every database read, every template
  variable, every JS computation. At each node, list the inputs and
  the transformation. Show this as a tree or as a numbered list, not
  as prose.
- For each branch in the tree, identify which Phase 3 finding (if any)
  applies. If no Phase 3 finding applies, that branch is either
  innocent or the audit missed something; mark it for re-investigation.
- Best-evidence root cause: which combination of findings most likely
  explains the symptom. State this as a hypothesis with citations,
  not as a conclusion.
- Verification plan: list the queries, code reads, or hand-computed
  examples that would confirm or reject the hypothesis. Do not run the
  app or modify code; this is documentation of how to confirm.

Required symptoms to investigate (developer-supplied):

1. Projected end balance for the current pay period displays as $160
   on the grid; checking account balance on `/savings` displays as
   $114.29. Both should be the same number computed from the same
   inputs.
2. Mortgage payment amount on the amortization schedule has been
   observed at $1911.54, $1914.34, and $1912.94 on different views or
   different sessions. Updating the current principal in loan
   parameters on `/accounts/3/loan` changes it to $1910.95.
3. The current principal on the mortgage account does not appear to
   update as transfers to the mortgage account are made. The developer
   expects confirmed transfers (settled shadow income on the loan
   account) to reduce the stored or computed real principal.
4. The monthly payment on a 5/5 ARM is fluctuating by a few dollars
   over consecutive months despite being inside the fixed-rate window.
   This must not happen.
5. Account balances on `/accounts` do not match the balances shown
   anywhere else in the app.

Each symptom is its own subsection in the symptoms document.

## 6. Phase 6: DRY and SOLID audit

Output: `docs/audit/financial_calculations/06_dry_solid.md`.

The earlier phases focused on correctness. This phase focuses on structure. The two are connected:
duplication is the substrate on which silent drift grows.

For each finding in this phase, cite the relevant principle and the file/line evidence.

### 6.1 DRY violations in calculation logic

List every case where the same calculation appears in two or more places. Examples to look for, but
do not limit the audit to:

- Two services compute the same concept with copied formulas.
- A service computes a concept; a template recomputes a closely
  related concept with Jinja arithmetic.
- A service computes a concept; a JS file recomputes it for charts.
- Two helpers with similar names (`_sum_remaining`, `_sum_all`,
  `_sum_settled_expenses`, `_sum_paid_expenses`) that share most of
  their structure but vary by filter. These should likely share a
  parameterized core.
- Status filters expressed inline as `txn.status_id != projected_id`
  in many places, rather than centralized.
- Effective-amount selection (`actual if actual is not None else
  estimated`) reproduced inline in multiple files.

For each duplication, recommend (in the report only, not in code) a single source of truth. Do not
refactor.

### 6.2 SOLID violations in service design

For each service file, evaluate against:

- **Single Responsibility.** Does the file do one thing, or does it
  mix HTTP, business logic, and data access? The roadmap already
  flags `savings.py:dashboard` as a 470-line SRP violation; verify
  the current state and find others. List every service or route
  function over 200 lines that mixes concerns.
- **Open-Closed.** Does the file branch on AccountType strings or
  enum names rather than on metadata flags like `has_amortization`?
  These are open-closed violations; the prior audit identified some
  of these and the roadmap states they are addressed, so the audit
  must verify the current state by grep, not by trust.
- **Liskov.** Are calculation services that handle multiple account
  types treating them uniformly through a common interface, or do
  they branch on subtype in ways that would break if a new type were
  added?
- **Interface Segregation.** Do helpers take large opaque parameter
  bags (`ctx`, `base_args`) when only a few fields are used? List
  cases where this hides what a function actually depends on.
- **Dependency Inversion.** Do services depend on concrete model
  classes when they could depend on plain-data DTOs? `PaymentRecord`
  is an example of doing this right; find places that do not.

### 6.3 Boundary violations

The architecture is `Routes -> Services -> Models / Schemas`. Services are forbidden from importing
`request`, `session`, `current_app`, or any Flask object. Grep services for these imports and list
every violation. The transfer invariants forbid services other than the transfer service from
mutating shadow transactions or from querying `budget.transfers` for balance computation; verify
both.

## 7. Phase 7: Test coverage gaps for financial assertions

Output: `docs/audit/financial_calculations/07_test_gaps.md`.

A financial calculation that has no hand-verified test is a calculation the developer has no
leverage to fix without breaking. This phase catalogs the test coverage state.

For each concept in the catalog, list:

- Tests that assert specific dollar values for that concept. Cite
  test file, test name, and the asserted value.
- Tests that assert relationships between concepts (`net = gross -
  taxes - deductions`).
- Whether the tests are pinned (assert exact Decimal values) or loose
  (assert approximate or boolean conditions).
- Coverage gaps: concepts with no pinned-value tests, edge cases
  identified in the inventory that are not tested, and consistency
  invariants that are not tested.

For each consistency finding from Phase 3 with verdict DIVERGE, identify whether a test exists that
would have caught the divergence. If not, propose (in the report only, not as code) a test that
would.

The test the developer most needs but probably does not have is a cross-page consistency test: a
fixture that sets up an account with known transactions, then asserts that every page-facing service
produces the same balance for the same period. Note this gap explicitly even if individual concept
tests exist.

## 8. Phase 8: Findings report

Output: `docs/audit/financial_calculations/08_findings.md`.

This is the developer-facing summary. It distills the prior seven phases into a prioritized list of
findings.

Each finding has:

- ID (F-001, F-002, ...).
- Severity: CRITICAL, HIGH, MEDIUM, LOW.
- Category: drift, source-of-truth, DRY, SOLID, test gap, definition.
- One-paragraph description in plain language. The developer must be
  able to read this paragraph alone and understand the issue.
- File/line evidence.
- Pointer to the phase document(s) that contain the full analysis.
- Open questions, if any.

Severity rubric:

- **CRITICAL** -- the finding can produce a wrong dollar amount on a
  page the developer relies on for budgeting decisions, and the
  divergence is not visible to the user as an error. The reported
  symptoms about checking balance, mortgage payment, and current
  principal are likely CRITICAL.
- **HIGH** -- structural duplication or stored/computed drift that
  has not yet produced an observed wrong number but is sufficient to
  do so under realistic conditions.
- **MEDIUM** -- DRY/SOLID violations, missing tests for important
  invariants, definition ambiguity in non-customer-facing places.
- **LOW** -- formatting, naming, minor duplication with low blast
  radius, places where two paths happen to agree but only by
  coincidence.

Sort the findings by severity, then by the developer's reported symptoms (so CRITICAL findings
related to the reported symptoms are at the top).

Do not propose fixes here. Each finding may include a "remediation direction" sentence, but the
actual fix planning is a separate exercise the developer will conduct in a later session.

## 9. Phase 9: Open questions

Output: `docs/audit/financial_calculations/09_open_questions.md`.

Throughout the audit, every place the auditor asked "what is the intended behavior here?" goes into
this file. Each entry has:

- Question, written so the developer can answer in one or two
  sentences without needing to read the audit.
- Why it matters: which finding(s) depend on the answer.
- Where the question came up: file/line and phase document section.

Resolve nothing. The developer answers these in a follow-up session and the auditor revises the
findings then.

Plan-vs-code questions are particularly important to surface in this file. When the priors document
captured an intent claim from a plan and Phase 3 found the code does something else, the question
for the developer is which side is correct: should the code be brought into line with the plan, or
has the plan been overtaken by a better idea that the code now reflects? The auditor does not
choose; the developer does. Phrase each such question with both citations: the plan's section and
the file/line where the code disagrees, so the developer can adjudicate without having to re-read
either source from scratch.

## 10. How to run this with Claude Code

The audit is large enough that it will not fit in one Claude Code session. Run it in phases, each in
its own session, in order.

### 10.1 Session structure

Each session has a single phase as its only goal. The session's first prompt is the corresponding
section of this plan, copied verbatim, plus a one-paragraph reminder of the hard rules from section
0. The session's last act is to write the phase output file and stop.

Do not let one session bleed into the next. If the phase output file is not written, the session is
incomplete; rerun it.

### 10.1a Phase 0 session breakdown

Phase 0 is broken into sub-phases. Run them as separate sessions, each producing its piece of the
priors document by appending to the file. The recommended split:

| Session | Goal                                                              | Expected length |
| ------- | ----------------------------------------------------------------- | --------------- |
| P0-a    | Triage of `docs/` (sub-phase 0.1) and the standards-and-roadmap reading (0.2) | 100-200 lines added to priors |
| P0-b    | Behavioral expectations (0.3), expanded with anything found in standards reading | 50-100 lines added |
| P0-c    | Plan skim for the priority plans plus any RELEVANT_CURRENT plans surfaced by triage (0.4) | 100-200 lines added |
| P0-d    | Prior audits (0.5) and patches/commits (0.6) | 50-100 lines added |

The priors document accumulates across these sessions. Each session reads the document state from
the prior session, appends its piece, and stops.

If P0-c balloons because the priority plans are individually huge, split it further: one session per
plan in skim mode. The hard rule holds: skim for behavioral claims, do not summarize. If a session
produces a 1000-line plan summary, it failed; rerun it with a tighter prompt.

After P0-d completes, do a single short review session to read the full priors document end-to-end,
check it against the target length of 200-500 lines, and tighten anything that drifted into prose.
This review session does not add new content; it only edits the existing file for concision.

### 10.1b Permission mode: run every session in plan mode

Claude Code has a permission mode named `plan` that restricts the agent to read-only operations:
file reads, grep, glob, and read-only Bash commands are allowed; file writes, edits, and
code-modifying tools are blocked at the tool layer. This is the correct mode for every session of
this audit. It enforces the read-only constraint mechanically, not just by prompt instruction.

Start every audit session with:

```text
claude --permission-mode plan
```

Or, after starting normally, press `Shift+Tab` to cycle into plan mode before sending the first
message. The mode indicator at the bottom of the prompt confirms the session is in plan mode. Plan
mode also tells Claude Code to use a research-oriented internal subagent when it explores the
codebase, which keeps file contents out of the main session's context.

Plan mode permits writing the audit's own output files (`00_priors.md`, `01_inventory.md`, etc.)
when those writes are explicitly part of the task. If a write attempt is blocked, investigate why
the agent is trying to write outside the audit directory rather than working around the block by
switching modes.

The `--dangerously-skip-permissions` flag and the `bypassPermissions` mode are never appropriate for
this audit. They disable the protection that keeps the audit honest.

### 10.1c Use the built-in Explore subagent for read-heavy phases

Claude Code includes a built-in subagent named **Explore**: read-only by design (Write and Edit
tools denied), runs on Haiku for speed, purpose-built for codebase search and analysis. Claude
delegates to Explore automatically when it needs to read many files without making changes. This is
exactly the workload of Phase 1 (inventory across services, routes, models, templates, JS) and parts
of Phase 6 (DRY/SOLID grep) and Phase 7 (test-coverage scan).

For these phases, instruct the main session to delegate to Explore explicitly. Example prompt for
Phase 1:

> Use the Explore subagent to inventory `app/services/` according to Phase 1.1. Read every service
> file and return a structured summary in the format the plan specifies. Then run a second Explore
> for `app/routes/` per Phase 1.2. Then a third for `app/models/` per Phase 1.5. Aggregate the three
> summaries into `01_inventory.md`. Specify thoroughness `very thorough` for each invocation.

The advantage of explicit delegation is that the verbose file contents stay in the subagent's
context, and only the structured summary returns to the main session. This keeps the main session's
context window from filling with raw file content during the inventory and is the only way Phase 1
fits in a reasonable number of sessions.

Phases 0, 2, 3, 4, 5, 8, and 9 are synthesis phases that build on prior findings and require the
accumulated context; the main session runs them directly.

If the audit ever needs a more constrained subagent than Explore (for example, one that strictly
cannot execute Bash at all), define a project-level subagent in `.claude/agents/` with a tight
`tools` field. For this audit, Explore as-shipped is sufficient.

### 10.1d Reference files with @ and verify by grep

Two small habits that significantly improve accuracy:

**Reference files with `@` in every prompt.** When a session's prompt names a file, write the path
with the `@` prefix so Claude Code reads the file before responding: `@CLAUDE.md`,
`@docs/project_roadmap_v4-6.md`, `@docs/audit/financial_calculations/00_priors.md`. The audit
prompts should be modified at copy-paste time to use `@` for whichever specific files are relevant
to the phase. Without `@`, the agent may rely on a stale snapshot or summarize from earlier
conversation context.

**Verify by grep before asserting.** When the auditor is about to claim that something exists in the
codebase ("`current_principal` is read in three files") or does not exist ("no service queries
`budget.transfers` directly"), the supporting `grep` or `glob` must run first. The plan asks for
file/line citations everywhere; the practical mechanism for producing citations is
`grep -rn <pattern> app/`. Recall from memory is unreliable; grep is mechanical and verifiable. The
anti-shortcut prompt below incorporates this rule.

### 10.1e Name sessions for easy resume

The audit will span many sessions across days or weeks. Use `/rename` (or pass `--session-name` at
startup) to label each session by phase. Suggested names: `audit-p0-a`, `audit-p0-b`,
`audit-p1-services`, `audit-p1-routes`, `audit-p3`, and so on. When you need to resume,
`claude --resume <name>` finds the session without scrolling through a picker.

After completing each phase, run `/clear` (or start a brand-new session for the next phase) so
accumulated context from the prior phase does not bleed into the next one. The phase output files
are the durable state; the session context is disposable.

### 10.1f Do not modify CLAUDE.md for this audit

The project's existing `CLAUDE.md` is concise and focused on production rules. Adding audit-specific
instructions to it would bloat the file and degrade Claude Code's adherence to existing rules; the
official guidance is explicit that overly long `CLAUDE.md` files cause Claude to ignore actual
instructions.

The audit-specific instructions belong in the session prompts, not in `CLAUDE.md`. The anti-shortcut
prompt in section 10.4 is what Claude Code reads at the top of each session; it is the right place
for audit-only constraints.

If you want to keep audit-only instructions handy without checking them into the repository, place
them in `CLAUDE.local.md` (`.gitignore` should exclude this file by default). It is automatically
read alongside `CLAUDE.md` but stays personal-scope. After the audit completes, delete it.

### 10.2 Context priming for each session

At the start of every session after the first, instruct Claude Code to read the priors document
(`00_priors.md`) and every phase output that has already been written, before starting the new
phase. The later phases reference earlier phases; without the context, they cannot cite or build on
them.

### 10.3 Required reading per session

| Phase | Must read first                                              |
| ----- | ------------------------------------------------------------ |
| 0     | Sub-phases 0.1 through 0.6                                   |
| 1     | `00_priors.md`                                               |
| 2     | `00_priors.md`, `01_inventory.md`                            |
| 3     | `00_priors.md`, `01_inventory.md`, `02_concepts.md`          |
| 4     | All of the above                                             |
| 5     | All of the above plus `03_consistency.md`, `04_source.md`    |
| 6     | `00`, `01`, `02`                                             |
| 7     | All of `00` through `05`                                     |
| 8     | All prior phase outputs                                      |
| 9     | All prior phase outputs                                      |

### 10.4 Anti-shortcut prompts

At the top of every session, paste the following into the prompt:

> This session is part of a read-only audit running in Claude Code's `plan` permission mode.
> Document findings in the phase's Markdown output file with file and line citations to the actual
> source. Read the relevant function fully before drawing conclusions about its behavior. Verify
> every factual claim about the codebase by running `grep`, `glob`, or another mechanical search; do
> not recall from memory. Reference files with `@` so they are actually read rather than summarized
> from prior context. Use the Explore subagent when an investigation would otherwise read many
> files, so verbose contents stay out of the main session. When a calculation's intended behavior is
> unclear, add the question to `09_open_questions.md` and stop on that item. Findings go into the
> phase output file; source files, tests, and migrations remain untouched. Stay within the assigned
> phase.

This phrasing is deliberate. The developer's prior experience is that shortcuts compound; the prompt
is structured to deny them while giving the agent positive instructions about what to do instead.

### 10.5 What to do if a session runs out of context

The phase output file is the recovery state. If a session truncates, inspect what was written,
identify the next item in the plan that has not been addressed, and start a new session focused on
just that item, with the prior partial output as input. Do not restart the phase from scratch; that
risks producing different findings on the same code.

### 10.6 What to do if a session produces a fix

It will try, eventually. The developer's prior experience is that the agent will see something and
want to "just fix the obvious bug." When it does, treat the diff as a finding artifact, not a fix:
extract the diagnosis from the agent's reasoning, add it to the findings document with the proposed
remediation as a "remediation direction" sentence, and revert the diff. Do not merge any diff
produced during the audit.

### 10.7 Scope policing

If a session starts to drift into out-of-scope territory (rewriting non-financial routes,
refactoring templates for cosmetics, changing auth code), stop the session. Re-prompt with the scope
boundary from section 0 and the specific phase task. Drift is the most common audit failure mode for
solo developers; the only defense is to halt sessions that drift and retry with a tighter prompt.

### 10.8 Recognize known Claude Code failure patterns

The official Claude Code best-practices documentation identifies a small set of recurring failure
patterns. Each one applies to this audit; watch for them and apply the remedy promptly.

- **Kitchen sink session.** A session starts on Phase 1 inventory,
  drifts into commenting on Phase 3 consistency findings, then
  circles back. The context fills with mixed material and later
  reasoning suffers. Remedy: `/clear` between phases. One phase
  per session as section 10.1 specifies.
- **Correcting over and over.** The agent produces a finding that
  misclassifies a calculation. The auditor corrects it. The next
  finding makes a related mistake. After two corrections on the
  same kind of issue, context is polluted with failed approaches.
  Remedy: stop the session, `/clear`, restart with a tighter
  prompt that incorporates what you learned about the
  misclassification.
- **Trust-then-verify gap.** A phase output file looks plausible
  but contains claims unsupported by file/line citations. This is
  the trap the audit is supposed to surface in the codebase, and
  the same trap applies to the audit's own output. Remedy:
  spot-check every phase output by picking 5-10 claims at random
  and verifying that the citations resolve to the expected code.
- **Infinite exploration.** Phase 1 tells the agent to inventory
  every location that produces or consumes a financial figure.
  Without scoping, the agent reads hundreds of files and the
  context fills before any output is written. Remedy: Phase 1
  uses Explore subagents per layer (services, routes, models,
  templates, JS) so each layer's exploration stays in its
  subagent's context. Section 10.1c is mandatory, not optional.
- **Over-specified instructions.** The temptation, once a session
  drifts, is to keep adding rules to the prompt. Past a certain
  length the agent ignores parts of the prompt. Remedy: keep the
  anti-shortcut prompt in section 10.4 short and specific; add
  phase-specific guidance separately rather than piling onto the
  same paragraph.

If a failure pattern recurs even after the remedy, stop and reconsider whether the phase's prompt is
too broad. A broader scope rarely helps; tighter scope almost always does.

## 11. Acceptance criteria

The audit is complete when all of the following are true.

1. `docs/audit/financial_calculations/` contains files `00_priors.md`
   through `09_open_questions.md`, each non-empty and each meeting
   its phase deliverable.
2. Every concept used by any page in the app appears in the concept
   catalog.
3. Every multi-implementation concept has a Phase 3 finding with a
   verdict (AGREE, DIVERGE, or UNKNOWN). UNKNOWN counts as a finding
   that requires developer input.
4. Every developer-reported symptom has a Phase 5 entry with a
   hypothesis tree and a best-evidence root cause.
5. The findings document is sorted by severity and includes file/line
   evidence for every finding.
6. The open questions document contains every place the auditor was
   uncertain about intended behavior.
7. No code, no tests, no migrations, no templates, and no static
   files have been modified by the audit. `git status` shows only the
   new files under `docs/audit/financial_calculations/`.
8. Every audit session was launched with `claude --permission-mode
   plan`, providing tool-layer enforcement of the read-only
   constraint in addition to the prompt-level instructions.

When all acceptance criteria are met, the developer reads the findings document, answers the open
questions, and the next phase of work (remediation planning) begins as a separate exercise outside
the scope of this plan.

## 12. Appendix A: Controlled vocabulary starter set

Use these tokens to label financial concepts in the inventory and consistency documents. Add new
tokens as needed, but do so explicitly at the top of the inventory so later phases can grep them.

```text
checking_balance
account_balance
projected_end_balance
period_subtotal
loan_principal_real
loan_principal_stored
loan_principal_displayed
monthly_payment
principal_paid_per_period
interest_paid_per_period
escrow_per_period
payoff_date
months_saved
total_interest
interest_saved
apy_interest
growth
employer_contribution
contribution_limit_remaining
ytd_contributions
paycheck_gross
paycheck_net
taxable_income
federal_tax
state_tax
fica
pre_tax_deduction
post_tax_deduction
transfer_amount
effective_amount
goal_progress
emergency_fund_coverage_months
dti_ratio
net_worth
savings_total
debt_total
chart_balance_series
year_summary_jan1_balance
year_summary_dec31_balance
year_summary_principal_paid
year_summary_growth
year_summary_employer_total
```

## 13. Appendix B: Required greps

These are the minimum grep patterns the inventory phase must run. Record the file/line of every
match in the inventory. The list is not exhaustive; add patterns whenever the audit suggests them.

```text
calculate_balances
calculate_balances_with_interest
calculate_balances_with_loan
calculate_monthly_payment
calculate_remaining_months
calculate_interest
calculate_employer_contribution
calculate_investment_inputs
calculate_paycheck
project_balance
reverse_project_balance
generate_schedule
generate_projection_periods
amortization_engine
balance_calculator
chart_data_service
savings_dashboard_service
year_end_summary_service
retirement_dashboard_service
dashboard_service
recurrence_engine
paycheck_calculator
tax_calculator
seasonal_forecast
smart_estimate
anomaly_detection
effective_amount
estimated_amount
actual_amount
current_principal
original_principal
current_anchor_balance
current_anchor_period_id
is_arm
rate_history
PaymentRecord
shadow
transfer_id
status_id
StatusEnum
TxnTypeEnum
ROUND_HALF_UP
Decimal\(
quantize
func.sum
func.avg
SUM\(
AVG\(
```

End of plan.
