# Budget App -- Project Roadmap v5

**Version:** 5.4 **Date:** July 19, 2026 **Parent Documents:** project_requirements_v2.md,
project_requirements_v3_addendum.md **Supersedes:** project_roadmap_v4-6.md (preserved for
historical reference)

---

## Overview

### What changed in v5.4 (this revision, July 19, 2026)

v5.3 realigned the roadmap with shipped code; v5.4 slims it down. A developer-ratified pruning pass:
the future-work sections had grown into full specifications that rotted as the app evolved
underneath them. Unlike prior revisions, this one deliberately removes spec text -- the full
Sections 3-5 specifications as of v5.3 remain in git history (commit bb879797) and are superseded,
not lost.

1. **Sections 3-5 shrunk to intent + priority + pointer.** Each now carries a banner: the detailed
   spec was written in April 2026, before the posting ledger and the UI overhaul, and a fresh plan
   of record (the Section 7 pattern) must be written before building.

2. **Pruned.** 3.4 deduction inflation folded into 3.3; 3.10 bill due-date optimization demoted to
   Appendix B (detection and the move action already shipped; only the heavy recommender was
   missing); 5.2 PDF export replaced by print stylesheets; 4.4 email delivery replaced by
   self-hosted push (ntfy) with no mail-server dependency. Notifications rescoped to the daily-check
   engine plus five core types, and the weekly digest becomes a period-close digest (pay periods are
   the app's native rhythm).

3. **Smart features re-ranked** to exploit accumulated actuals: 3.2 rolling average -> 3.12
   anomaly-at-entry -> 3.9 confidence indicator -> 3.1 seasonal, rebuilt as auto-learn from actuals
   (the manual history-entry grid is dropped). Five candidates recorded: tax-withholding drift
   alert, safe-to-spend chip, price-creep watchdog, period-close digest, seasonal auto-learn.

4. **New Section 8 (Import and Reconciliation).** File-based CSV import for the developer's bank, a
   conservative matching and review flow driving the existing settle seams, and running-balance
   assertions against the balance seam; SimpleFIN Bridge as a later bolt-on fetcher. Slotted after
   Section 7 and before Section 3, because imported actuals feed every smart feature.

5. **New Section 9 (Operations).** The operational track made visible: the Argon2id migration and
   config-drift script (Section 1 Phase 8 orphans), a backup restore drill, production monitoring,
   dependency and Postgres upgrade cadence, and the blocked flask-login upstream bump.

6. **Numbering stays chronological** (8 and 9 execute ahead of 3-6; the execution-order table is the
   priority source). A future v6 reorganisation will renumber once the balance arc and the
   credit-card arc close.

### What changed in v5.3 (July 19, 2026)

This revision records the largest architecture reversal in the project's history, marks the UI/UX
overhaul complete, and adds the next feature arc. As with prior revisions, no future-work specs were
deleted.

1. **Section 2 Stage B (double-entry ledger): the deferral was reversed and the ledger was built.**
   One day after v5.2 deferred Stage B indefinitely, the revisit valve fired: balance divergence
   reappeared (net-worth views valuing a loan's pre-payment periods at original principal, PRs
   #43/#44), and the follow-up investigation concluded the recurring balance and net-worth defects
   were architectural -- the app had no single balance-at-T seam. The Option D decision record chose
   a staged build guarded by parallel-run oracles instead of the big-bang migration the ROI review
   had priced. Between June 27 and July 4 the whole program shipped to production: the `balance_at`
   seam (Level 1), the append-only posting ledger piloted on transfers, cash/envelope postings, loan
   real-split postings, the loan read switch, and confirmed-ledger financial statements. A follow-on
   "fail-loud ledger authority" arc (complete through C9 on `dev`) rebuilt the loan balance as a
   total fold over an event stream with the payoff date derived, never stored. Section 2.2 is
   rewritten accordingly; the arc's tail is the current active work.
2. **UI/UX overhaul marked COMPLETE.** Every screen on the rollout list has been rebuilt under the
   Steel Ink design language and developer-accepted, and the cross-page polish pass (Waves 0-2,
   sessions S1-S16) closed its register. Shipped to production through PR #63 (July 13). Small
   residue is listed in the section.
3. **New Section 7 (Credit Card Accounts).** An approved plan of record (July 19, 2026) replaces the
   phantom-payback Credit workflow with a real revolving credit-card account. It executes ahead of
   Sections 3-6, gated on the balance-arc tail, the interim prod ship, and X1.
4. **Execution order updated.** Near-term: balance-arc tail (Phase D; C9 shipped July 19) -> interim
   prod ship -> X1 -> credit-card arc (Phases 0-5) -> balance-arc X2-X4/E1/F1 closeout ->
   credit-card UI. Sections 3-6 (Smart Features, Notifications, Data Export, Multi-User) resume
   afterward, unchanged in content and relative order.
5. **Appendix A extended (A.21 through A.26)** with the completed work: the balance architecture
   program, the escrow configuration redesign, the analytics/taxes overhaul, the per-screen UI
   rollout completion, the UI/UX polish pass, and test-isolation/tooling hardening.

### What changed in v5.2 (June 25, 2026)

This revision re-prioritises post-overhaul work after a fresh ROI review grounded in the current
code, and surfaces the in-progress UI/UX overhaul in the execution order. No future-work specs were
deleted; the changes are status and priority corrections.

1. **UI/UX overhaul surfaced as in-progress.** The Fable 5 per-screen rebuild is now shown as active
   work in the execution order and in a dedicated in-progress section, not only in the
   completed-work appendix (A.19). Grid and Dashboard are complete; Accounts is in progress; the
   remaining screens are planned.
2. **Stage B (double-entry ledger) deferred indefinitely.** An ROI review found it the
   highest-effort, highest-risk item for no user-facing gain, while Stage A already holds balances
   correct under the HIGH-01 lock. The revisit valve is retained (Section 2.2).
3. **Stage C (envelope budgeting) dependency corrected, then deferred.** The long-standing claim
   that envelopes require Stage B is wrong: the entry-aware math already runs on
   `budget.transactions` via E-25 and Stage C is a single additive table, so envelopes are
   standalone-capable. Deferred pending confirmed need for category-level allocation (Section 2.3).
4. **Execution order after the overhaul:** Section 3 (Smart Features) next, then Section 4
   (Notifications, sequenced so alerts can build on Smart Features), then Section 5 (Data Export,
   low priority), then Section 6 (Multi-User, on the table but deferred until the app is ready to
   share).

### What changed in v5.1 (this revision, June 14, 2026)

This revision realigns the roadmap with the code that has actually shipped since v5.0. No
future-work specs were deleted; the changes are status corrections and additions to the
completed-work appendix.

1. **Section 1 (Security Remediation) re-scored.** v5.0 reported "16 of 56 commits merged (~29%)."
   The true state: Phases 1-7 are complete, Phase 8 (low/info cleanup) findings were consolidated
   into earlier commits (C-40, C-44, C-45, C-46) and largely shipped, two Phase 8 items remain
   (Argon2id password-hash migration and a config-drift check script), Phase 9 (C-53..C-55) and C-39
   are deliberately not being pursued, and Phase 10 is operator-tracked. The status was verified
   against merged git history, not the plan document.
2. **Section 2 (Financial Calculation Consistency): Stage A marked COMPLETE.** The May-June 2026
   financial-calculation audit (25 findings) was remediated; canonical balance/loan producers and a
   cross-page regression lock shipped. Stages B and C are deferred with explicit revisit criteria.
3. **Appendix A extended (A.11 through A.20)** with everything completed since v5.0: carry-forward
   aftermath and envelope view, test performance and per-worker databases, mobile v3, the
   amortization engine split, the homelab security audit, the code quality audit and remediation
   (pylint 10.00/10 plus quality-pass plus deep-quality-hunt), the polyglot standards cleanup,
   dev/prod container parity, the Fable 5 UI/UX overhaul, and pay-period CRUD with a rolling window.
4. **Folded in the unmerged "v5.1" draft.** A prior v5.1 status update was written on branch
   `claude/busy-thompson-rEhgG` (May 21, 2026) but never merged. Its still-valid decisions (the
   security descope rationale and the Stage B/C deferral criteria) are carried into this revision;
   the draft is otherwise superseded.

Future Sections 3-6 (Smart Features, Notifications, Data Export, Multi-User) are unchanged from
v5.0.

### What changed in v5

This version is a structural reorganisation of the roadmap; almost no content was deleted.

1. **Numbering collapsed.** v4-6 carried two parallel numbering systems -- a "Priority N" track in
   the summary table and a "Section M / Phase Y" track in the document body. The two had drifted
   (Priority 4 mapped to Section 8, Priority 5 mapped to Section 6, Section 9 claimed Priority 8
   while the priority table also assigned Priority 8 to Multi-user). v5 drops the Priority column
   and uses a single sequential top-level numbering scheme ordered by execution priority. Subsection
   numbers follow their new parent section (former 6.x is now 3.x, former 7.x is now 4.x, former
   8A.x is now 5.x), so internal cross-references stay aligned with the sections they point to.
2. **Completed work moved to Appendix A.** All sections marked complete in v4-6 plus the newly
   completed Visualization and Reporting Overhaul and Spending Tracker and Companion View are
   summarised in Appendix A under their original section labels. Full historical detail remains in
   `project_roadmap_v4-6.md`.
3. **Two new sections added.**
   - **Section 1 (Security Remediation):** the in-progress April 2026 audit response (56 commits
     across 10 phases; 16 merged). Entry summarises status and links to the canonical plan at
     `shekel-audits/security-2026-04-15/remediation-plan.md`; the plan is not duplicated in the
     roadmap.
   - **Section 2 (Financial Calculation Consistency):** a new parent section addressing drift
     between the multiple paths that compute monetary amounts. Three sequenced stages: unify
     existing paths (committed), double-entry ledger refactor (decision pending), and envelope
     budgeting layer (decision pending, requires the ledger).

### Production status

The app moved to production on March 23, 2026. It runs as a Docker container on an Arch Linux
desktop, with internal access via Nginx and a DNS override, and external access via a Cloudflare
Tunnel. The primary focus is now stabilization, daily-use polish, security hardening, and
incremental feature development. As of July 19, 2026, production runs the July 13 build (PR #63);
`dev` carries the fail-loud ledger arc awaiting its F3 prod ship (Section 2.2).

### Completed phases

See **Appendix A** for the complete list of completed work, including all v3 phases, the
post-production roadmap completions through April-May 2026, and unplanned work. Each entry preserves
its original v4-6 section label for cross-reference.

### Deferred indefinitely

| Source Document | Phase     | Reason                                   |
| --------------- | --------- | ---------------------------------------- |
| v3 Phase 7      | Scenarios | Effort not worth the reward at this time |

See **Appendix B** for the full deferred-items reference.

---

## Roadmap -- Execution Order

| Section | Title                                | Status              | Summary                                                                                                  |
| ------- | ------------------------------------ | ------------------- | -------------------------------------------------------------------------------------------------------- |
| 1       | Security Remediation                 | Mostly complete     | Phases 1-7 done; Phase 8 low/info findings consolidated and largely shipped; Argon2id migration + config-drift script remain; Phase 9 and C-39 not pursued; Phase 10 operator-tracked. |
| 2       | Financial Calculation Consistency    | Stage B built; tail ACTIVE | Stage A shipped. Stage B's deferral was reversed June 26 and the posting ledger + `balance_at` seam program is in production; the fail-loud ledger arc is complete through C9 on `dev`. Tail (Phase D, F3 prod ship, X1-X4, E1, F1) is the current active work. Stage C (envelopes) still deferred. |
| UI/UX   | Fable 5 UI/UX Overhaul               | COMPLETE (July 2026) | Per-screen rebuild and the cross-page polish pass complete, developer-accepted, shipped through PR #63. Small residue tracked in `docs/design/ui_ux_polish_audit.md`. |
| 7       | Credit Card Accounts                 | Approved -- next feature arc | Real revolving credit-card account replaces the phantom-payback Credit workflow. Plan of record approved July 19, 2026; gated on the balance-arc tail, the interim prod ship, and X1. |
| 8       | Import & Reconciliation              | Planned -- after Section 7 | One-bank CSV adapter, conservative match + review driving the existing settle seams, running-balance assertions vs the balance seam; SimpleFIN bolt-on later. Feeds Section 3. |
| 3       | Smart Features                       | After Section 8     | Re-ranked: rolling average, anomaly-at-entry, confidence indicator, seasonal auto-learn. Spec superseded -- fresh plan of record before build. |
| 4       | Notifications                        | After Smart Features | Daily-check engine + 5 core types (low balance, missed payment, period aging, large expense, period-close digest); push via self-hosted ntfy. Full catalog deferred. |
| 5       | Data Export                          | Planned -- low priority | CSV export (5.1). PDF replaced by print stylesheets; full backup demoted (operator dumps + restic cover solo use). |
| 6       | Multi-User / Kid Accounts            | On the table -- deferred | Schema ready; companion role from Appendix A.10 is a precursor. Deferred until the app is ready to share. |
| 9       | Operations                           | Standing track      | Argon2id, config-drift script, restore drill, prod monitoring, dependency/Postgres cadence, flask-login bump. Interleaves with feature work; not sequenced. |

**Near-term execution order (developer-ratified July 19, 2026;** the interleaving of the Section 2
tail and Section 7 is load-bearing, recorded in `docs/plans/implementation_plan_credit_card.md`):

1. Balance-arc tail: Phase D (D1-D3: engine cluster private, typed balances, fence shrunk to a smoke
   alarm). C9 (a loan cannot receive a payment dated at or before its origination) shipped July 19
   as C9a (recurrence start bound) + C9b (write-boundary guard), closing FU-5.
2. Interim prod ship: the `dev` -> `main` PR for the whole ledger arc (F3), including the
   outstanding C2 real-clone history-window live-render check.
3. X1 alone: a settled transaction counts from the instant it settled; lands the shared
   instant-partition fold core the credit-card arc consumes.
4. Section 7 credit-card arc, Phases 0-5 (landing into `dev` per phase).
5. Balance-arc closeout: X2-X4 (the cash account becomes the same event-stream fold), E1 (postings
   become a checked projection of the fold), F1 (Van Loan data correction).
6. Section 7 Phase 6 (the card cockpit UI) any time after Phase 5; then Section 8 (Import and
   Reconciliation), then Sections 3-6 resume.

---

## 1. Security Remediation

**Status:** Mostly complete. Phases 1-7 shipped; Phase 8 (low/info cleanup) findings were
consolidated into earlier commits and largely shipped; two Phase 8 items remain (the Argon2id
password-hash migration and a config-drift check script); Phase 9 is not being pursued; Phase 10 is
operator-tracked. Verified against merged git history. **Audit date:** April 15, 2026.
**Canonical plan:** `shekel-audits/security-2026-04-15/remediation-plan.md`.
**Supporting files in `shekel-audits/security-2026-04-15/`:** `findings.md`, `c-09-followups.md`,
`reports/`, `sbom/`, `scans/`.

### 1.1 Context

The April 2026 security audit identified 164 verified findings (1 Critical, 29 High, 52 Medium, 79
Low, 3 Info). The remediation plan prescribed 56 commits across 10 sequential phases and remains the
canonical execution document; this roadmap entry is a status pointer only. As the work landed,
several Phase 8 low/info commits were folded into earlier commits (for example, C-43 absorbed the FK
ondelete sweep, C-45 absorbed the retirement-Decimal and grid-None-check findings, C-46 absorbed the
except-narrowing sweep), so the original 56-commit count no longer maps one-to-one onto what
shipped. The phase summary below is the accurate status. Detail (per-commit scope, files,
migrations, tests, code snippets) lives in the plan file and is not duplicated here.

### 1.2 Phase Summary

| Phase | Commits      | Scope                                                                                                                                                  | Status   |
| ----- | ------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------ | -------- |
| 1     | C-01..C-12   | Crypto and history: SECRET_KEY history excise, session invalidation, MFA hardening (backup codes, TOTP replay, secret storage), rate-limiting, cookies | COMPLETE |
| 2     | C-13..C-16   | Audit log: DB-tier audit triggers rebuild, systematic `log_event()` rollout, off-host shipping, PII redaction                                          | COMPLETE |
| 3     | C-17..C-23   | Financial invariants: anchor balance optimistic locking, stale-form prevention, TOCTOU duplicate prevention, transfer state-machine guards            | COMPLETE |
| 4     | C-24..C-28   | Input validation and schema sync: Marshmallow Range sweep, boolean NOT NULL, boundary inclusivity, auth schemas, multi-tenant account_type guard       | COMPLETE |
| 5     | C-29..C-31   | Access-control consistency: cross-user FK re-parenting fix, analytics ownership checks, 404-everywhere unification                                     | COMPLETE |
| 6     | C-32..C-39   | Config and hardening: production configs, network topology, Docker hardening bundle, Postgres TLS, Docker secrets, field-level PII encryption          | C-32..C-38 done; C-39 not pursued |
| 7     | C-40..C-43   | Schema cleanup: migration backfill conventions, duplicate CHECK cleanup, salary + HYSA index/FK repair                                                 | COMPLETE |
| 8     | C-44..C-52   | Low/Info cleanup: verify_password hardening, grid robustness, retirement Decimal, narrow except blocks, Argon2id migration, config drift check         | Mostly done; findings folded into C-40/C-44/C-45/C-46; Argon2id + config-drift remain |
| 9     | C-53..C-55   | Bigger features: server-side sessions + WebAuthn, GDPR export and delete, process-memory key documentation                                             | NOT PURSUING |
| 10    | C-56         | Host runbook: out-of-repo host hardening (chmod, sysctl, auditd, sshd, GRUB, core dumps, AIDE, NTP, PAM)                                               | Operator-tracked |

### 1.3 Descoped commits

These were deliberately not pursued. The rationale is recorded here so a future revisit can pick up
the trail.

- **C-39 (field-level PII encryption):** The effort-to-benefit ratio is not favourable for a
  solo-operator, single-tenant deployment that already has disk-level encryption. Revisit if a
  multi-user or hosted-tier deployment becomes a goal.
- **C-53 (server-side sessions + WebAuthn):** Client-side signed cookies remain adequate for the
  current threat model and single user. Revisit if a session-revocation gap is observed in practice
  or WebAuthn becomes a daily-use requirement.
- **C-54 (GDPR export and delete):** Section 5 (Data Export) covers the user-initiated export side.
  A formal GDPR delete-request workflow is not warranted for a single-user deployment. Revisit if
  multi-user lands.
- **C-55 (process-memory key documentation):** Documentation-only commit; the operator already holds
  the key-handling mental model.

### 1.4 Remaining work

The earlier decision list (Redis vs. single-worker limiter, off-host log destination, HSTS preload,
HIBP source, and the rest) collapsed as Phases 1-7 landed. What is left:

- **Argon2id password-hash migration (Phase 8, F-088 + F-141):** bcrypt is still in use; an
  opportunistic on-login rehash to Argon2id is planned but not yet shipped.
- **Config-drift check script (Phase 8):** not yet shipped.
- **Phase 10 host hardening (C-56):** out-of-repo operator work (chmod, sysctl, auditd, sshd, GRUB,
  core dumps, AIDE, NTP, PAM), tracked by the operator directly.

Both remaining Phase 8 items are also carried on the Section 9 (Operations) track, so they stay
visible alongside the rest of the operational work.

The original Phase 3 -> Section 2 sequencing gate is satisfied: both have shipped.

---

## 2. Financial Calculation Consistency

**Status:** Stage A is COMPLETE and shipped. The May-June 2026 financial-calculation audit (25
findings: 5 Critical, 8 High, 7 Medium, 5 Low) was remediated through the main and follow-up commit
chains; the audit artefacts live in `docs/audits/financial_calculations/`. Stage B (double-entry)
was deferred indefinitely in v5.2, but the revisit valve fired the next day and the stage has since
been BUILT AND SHIPPED as the `balance_at` seam + posting ledger program (2.2); the program's
fail-loud tail is the current active work. Stage C (envelope budgeting) remains DEFERRED pending
confirmed need; Stage C does not depend on Stage B.

**Goal:** The app produces identical monetary values regardless of which view, route, or service
computes or displays them. Structural rules prevent new parallel computation paths from being
introduced.

This section has three stages: Stage A removed immediate inconsistency between existing computation
paths; Stage B re-founded balances on a single seam and a double-entry posting ledger (2.2); Stage C
is an independent feature deferred pending confirmed need, and it does not depend on Stage B.

### 2.1 Stage A -- Single source of truth for balances (COMPLETE)

**Problem:** Some views and services computed the same logical balance through different code paths.
The paths drifted, producing visible inconsistencies. Documented symptoms included the
$160 grid vs. $114.29 `/savings` mismatch, the ARM monthly payment creep inside the fixed-rate
window, cells that did not match the subtotal containing them, and the historic salary-profile vs.
grid net biweekly mismatch (Appendix A.1, task 3.3).

**Outcome (delivered):**

- A single canonical computation produces every balance, subtotal, and aggregate the app shows in
  the grid, dashboards, calendar views, year-end summary, and account detail pages.
- A single status-aware "effective amount" rule is the only way to read a transaction's contribution
  to a balance. The rule covers projected, paid, settled, credit, and cancelled statuses and
  resolves correctly for both estimated and actual values.
- A path-equivalence regression test suite asserts that every consumer of balance data produces the
  same result for the same inputs. A new code path that diverges fails the suite.
- The coding standards record the rule so future contributors do not introduce a parallel path.

**Evidence (May-June 2026):** A read-only audit produced 25 findings
(`docs/audits/financial_calculations/08_findings.md`, with the phase-by-phase artefacts `00`-`09`).
Remediation established canonical producers for the load-bearing concepts: E-18 (event-derived loan
resolver, backed by an append-only `loan_anchor_events` table), E-19 (date-anchored anchor resolver,
NULL state unreachable), E-25 (canonical entries-aware balance and subtotal producer, routed across
the grid, `/savings`, `/accounts`, the calendar, year-end, net-worth, investment, and retirement),
E-26 (centralised `round_money`, retiring the bare `.quantize()` sites that silently used banker's
rounding), and E-27 (balance-as-of-date). The HIGH-01 cross-page balance-equality regression test is
the structural lock that fails any future code path that bypasses the canonical producer; a static
guard test also forbids grid and accounts from re-deriving balances. The developer-reported symptoms
are resolved. Supporting docs: `remediation_plan.md` and `remediation_follow_up.md` in the same
directory.

### 2.2 Stage B -- Double-entry ledger (REVERSED AND BUILT, June-July 2026; tail ACTIVE)

**Problem (unchanged):** Even after Stage A, balances were derived from transaction records rather
than recorded directly. Every consumer re-derived "the balance at time T" its own way, so future
architectural changes risked re-introducing the consistency problem.

**The deferral, and why it was reversed.** v5.2 (June 25) deferred this stage indefinitely after an
ROI review priced it as a big-bang migration: re-point E-25 and every consumer, rework the financial
test suite, and carry a second source of truth through the window. The revisit valve ("revisit only
if balance divergence reappears...") fired the next day: net-worth views valued a loan's pre-payment
periods at original principal instead of current balance (fixed in PRs #43 and #44), and the
follow-up investigation concluded the recurring balance and net-worth defects were a class, not a
list -- the app had no single balance-at-T seam. The Option D decision record (in
`docs/audits/balance_architecture/`) replaced the big-bang plan with a staged build: put in one seam
first, then grow a real ledger under it one domain at a time, each step guarded by a parallel-run
oracle against the old producer before any read switches over.

**What shipped (all in production):**

- **Level 1 -- the `balance_at` seam (PR #45, prod June 27).** A single accessor produces every
  balance-at-T the app shows. The custom pylint checker W9906 (balance-producer-bypass) fences the
  seam so a new bypass path is a lint failure, and a per-kind cross-page oracle locks page
  agreement. Kind-correct balances followed (PR #47, prod June 28): interest-bearing accounts show
  accrued-interest balances on the grid and the obligations panel.
- **Level 2 -- the posting ledger (Steps 2-5, prod June 28 through July 4).** An append-only
  double-entry journal (`budget.journal_entries` + `budget.account_postings`, with a deferred
  balanced trigger enforcing SUM=0 and COUNT>=2 per entry) over a chart of `ledger_accounts`, grown
  one domain at a time, each with a backfill migration and a reconciliation oracle: transfers (Step
  2, PR #48), cash and envelope transactions (Step 3, PRs #49/#50), loan payments as real
  principal/interest/escrow splits from actual cash with effective-dated escrow (Step 4, PR #51),
  the loan read switch making the ledger authoritative for loan balances (PR #52), and
  account-anchor postings plus confirmed-ledger financial statements -- income statement and balance
  sheet with CSV export (Step 5, PR #58).
- **The July 2 adversarial review (R1-R10, PR #54 and follow-ups).** Settlement-time splits, dated
  corrections, database-tier append-only enforcement, executable oracle teeth, transfer guards on
  amortizing loans, and a package split of the custom checkers under their own 10.00/10 floor.

**The fail-loud ledger-authority arc (IN PROGRESS -- the current active work).** With the ledger
live, a second design pass asked why loan pages could still 500 or silently disagree: the honest
ledger read was a partial function, and the derived posting cache was being treated as truth. The
fix -- planned and tracked solely in `docs/audits/balance_architecture/README.md` (the arc forbids
new planning docs) -- rebuilds the loan balance as a TOTAL fold over one event stream (ORIGINATION |
ASSERTION | PAYMENT), exposed through the seam's `positions()`, `plan()`, and `events()` entries,
with the payoff date derived by folding to zero, never persisted from a schedule. Phases A (stop the
bleeding), B (the fold as oracle), and C1-C9 (the cutover: every reader folds, and a loan payment
cannot precede its loan) are complete on `dev` as of July 19, 2026.

**Remaining (the Section 2 tail; sequencing in the execution-order list above):** Phase D (D1-D3:
engine cluster private inside the seam package, distinct cash-flow vs net-worth balance types, W9906
shrunk to a smoke alarm), the F3 interim prod ship (gated on the outstanding C2 real-clone
history-window live-render check), X1-X4 (the cash account becomes the same event-stream fold:
settled-instant partition, the fold itself, anchor history as the past, the anchor cache
reconciled), E1 (postings become a checked projection -- `sum(postings) == fold(ACTUAL events)`
asserted at write time), F1 (the known-bad Van Loan history correction), and closeout. X5 (anchor
`effective_date`) is an optional feature, not a prerequisite. C9 (a loan cannot receive a payment
dated at or before its origination) shipped July 19 as C9a (the recurrence start bound, so the app
stops generating the shape) and C9b (the write-boundary guard), closing FU-5.

### 2.3 Stage C -- Envelope budgeting (DEFERRED; does not require Stage B)

**Problem:** Budget categories do not have first-class per-period spending allocations. Per-template
entry tracking from the Spending Tracker (Appendix A.10) addresses the most common variable-amount
budgets (groceries, fuel, and similar single-template envelopes) but does not extend to
category-level limits that aggregate across multiple templates.

**Outcome after Stage C:**

- Each budget category has a per-period allocation amount (the envelope).
- Per-envelope remaining balance is visible at a glance, computed from `budget.transactions` via the
  canonical balance producer (E-25); no journal table required.
- Optional hard-cap mode prevents spending past the allocation; soft-warning mode flags envelopes at
  risk.
- Existing per-template entry tracking continues to work; envelopes aggregate spending across
  templates within a category.

The data model might look something like:

```text
budget.category_budget_allocations
- category_id, pay_period_id, allocated_amount
- user_id, audit columns
```

**Dependency correction:** Earlier revisions listed Stage C as requiring Stage B. A June 2026 code
review found this is not true. The entry-aware reservation math already runs against
`budget.transactions` (`_entry_aware_amount` / `entry_checking_impact` in `balance_calculator.py`,
routed through E-25), transactions already carry `category_id`, and Stage C's data model is a single
additive table. Stage C can therefore be built standalone, at medium-low effort and low risk,
without the double-entry migration.

**Decision:** Deferred pending confirmed need. Per-template entry tracking (Appendix A.10) already
covers common single-template envelopes; the open question is whether category-level allocation
aggregating across multiple templates is needed. The case for is mental-model alignment; the case
against is added setup complexity. Revisit when cross-template category budgeting becomes a felt
need. It does not gate on, and need not be sequenced after, Stage B.

---

## UI/UX Overhaul (Fable 5) -- COMPLETE (July 2026; small residue tracked)

**Status:** COMPLETE. Every screen on the rollout list has been rebuilt under the Steel Ink design
language (not a reskin), developer-accepted, and shipped to production; the last UI ship was PR #63
(July 13). The cross-page polish pass that followed (Waves 0-2 and sessions S1-S16, July 8-12)
closed its register. The foundation and core are Appendix A.19; the rollout completion and the
polish pass are Appendix A.24 and A.25. Per-screen audits live in `docs/design/`; the polish
register (`docs/design/ui_ux_polish_audit.md`) remains the source of truth for residue.

**Per-screen rollout (all COMPLETE):** Grid; Dashboard; Accounts cockpit (`/savings` + `/accounts`
unified into the Net Worth Cockpit, later extended with the P-AC1 net-worth diverging stream and the
rebuilt property equity chart); Account detail (one cash-detail band page, PR #55); the Retirement
rebuild (PR #56); the Salary cockpit (PR #57); Analytics (Calendar, Taxes, Spending, Statements
under a four-pill shell); Recurring (templates + transfers + obligations unified onto one surface);
Loan detail (band grammar); Investment detail (band grammar); the app-wide navbar and brand (Steel
Ink ink band, silver coin, Besley 700 wordmark); Settings retheme.

**Remaining residue (small, tracked in the polish register and the closeout plan):** the per-user
theme-selector UI (Scope B); the app-wide designed 4xx/5xx error-fragment follow-up; the two S8
out-of-scope findings (the loan-cell anchor editor writes an inert row on amortizing loans; the
amortization-schedule page's raw badge markup); P-DT1 and the P-DB4 layout half; the dashboard's two
revisit-later presentational deviations and the anchor UTC-day-vs-Eastern bucketing ruling. The
companion view was never scheduled for a rebuild (slots in per developer call), and hover/focus/HTMX
in-flight states plus a few never-shot sub-pages were outside the audit's scope. The credit-card
cockpit page (Section 7, Phase 6) is future UI work that runs through the same shekel-design loop.

---

## 3. Smart Features

**Status:** Planned; resumes after Section 8 (Import and Reconciliation), whose actuals feed every
feature here. **Spec status:** the detailed v5.3 specification (data models, thresholds, UI copy)
was written in April 2026, before the posting ledger and the UI overhaul, and had rotted in
places -- it targeted the removed dashboard mark-as-paid flow and recommended the Credit workflow
Section 7 deletes. It is superseded; the full text remains in git history (v5.3, commit bb879797).
Write a fresh plan of record (the Section 7 pattern) before building.

**Intent:** make projections smarter from the actuals the app accumulates, and catch bad data at the
point of entry. The app's core value is projection accuracy; these features compound it.

**Build order (re-ranked July 19, 2026 -- cheapest first, no manual data entry):**

1. **3.2 Rolling average estimates** -- suggest template amounts from the last N settled actuals;
   suggestion-only, never auto-applied.
2. **3.12 Anomaly at entry** -- flag an actual that deviates from the expected amount by a threshold
   during mark-as-paid, with a two-step confirm. Grid flow only; the dashboard mark-as-paid surface
   no longer exists.
3. **3.9 Estimate confidence indicator** -- a three-tier signal on future transactions showing how
   trustworthy each estimate is (actuals-backed vs static).
4. **3.1 Seasonal forecasting, as auto-learn** -- learn each seasonal template's monthly curve from
   accumulated actuals once 12-18 months exist (import accelerates this). The v5.3 manual
   history-entry grid is dropped; no backfill chore. 3.11 (year-over-year comparison) rides along as
   display once the curve exists.

**Kept as intent, unscheduled:** 3.3 expense inflation (per-template opt-in; 3.4 deduction inflation
folds in here as a yearly open-enrollment prompt rather than an engine); 3.8 third paycheck
suggestions (the calendar already detects the months; this adds the actionable card); a NAMED
27-paycheck year (a biweekly owner gets one in some years, and once `pay_calendar:C14` makes the
per-year count a derived fact the grid and the paycheck page can say so -- was ledger row
`recurrence:F-5`, ruled a feature and not a defect 2026-09-03, `R-R54`).

**Dropped:** 3.10 bill due-date optimization, demoted to Appendix B. Detection (the low-balance
alert) and the action (the shipped period-move UI) already exist; only the recommender between them
was missing, and it is the heavy part.

**Recorded candidates (July 19, 2026; unscheduled):**

- **Tax withholding drift alert** -- the withholding-to-date producer and the filing-time liability
  engine already exist (Appendix A.23); one threshold check yields "tracking toward a
  refund/underpayment of $X."
- **Safe-to-spend chip** -- current balance minus committed obligations before the next paycheck;
  exact under the pay-period model, one producer plus a dashboard chip.
- **Price-creep watchdog** -- flags slow drift in a recurring actual (insurance up 8% over six
  months); falls out of 3.2's machinery.
- **Period-close digest** -- see Section 4; it is the notification face of these features.

Completed earlier: 3.5 budget variance, 3.6 annual expense calendar, 3.7 spending trends (all
Appendix A.9).

---

## 4. Notifications

**Status:** Planned, after Section 3. **Spec status:** the v5.3 specification (15 types in 6 groups,
full data model, email delivery) is superseded; full text in git history (v5.3, commit bb879797).
Write a fresh plan of record before building.

**Intent:** surface conditions the user would otherwise find by scanning the grid -- in-app first
(bell + `/notifications` page), with push for away-from-app urgency.

**Rescope (July 19, 2026):**

- **Engine first, five types.** Build the daily scheduled check plus: low projected balance, missed
  payment, unreconciled period aging, upcoming large expense, and a period-close digest. The
  remaining catalog (milestones, pace alerts, ARM reminders, template drift, and the rest) is
  deferred until the core five prove out (Appendix B).
- **The digest is period-close, not weekly.** The app's native rhythm is the pay period; when a
  period closes, digest planned-vs-actual, envelope leftovers, and anomalies, instead of a
  calendar-week summary.
- **Push replaces email (4.4 dropped).** Delivery is in-app plus self-hosted push via ntfy (or
  Gotify): one container, instant phone delivery, pairs with the existing PWA, and there is no mail
  server to run or secure. The email sub-phase and its delivery-window machinery are dropped
  (Appendix B).
- **Keep from the old spec:** the dedup, snooze, and auto-resolve semantics and
  settings-with-sensible-defaults ideas; re-derive the details in the fresh plan.

---

## 5. Data Export

**Status:** Planned, low priority. **Spec status:** superseded; full text in git history (v5.3,
commit bb879797).

- **5.1 CSV export (kept -- effectively the whole section):** transaction-level export for a date
  range with account/category/status filters. Cheap and broadly useful.
- **5.2 PDF export (dropped):** replaced by print stylesheets -- a `@media print` pass on the
  statement, loan, and year-end pages gets the shareable-document value without a PDF library
  (Appendix B).
- **5.3 Full data backup (demoted):** operator-level backup already exists (Postgres dumps + restic;
  the restore drill is a Section 9 item). An app-level export/restore matters mainly for portability
  or an eventual second user; revisit then (Appendix B).

---

## 6. Multi-User / Kid Accounts

**Status:** On the table, but deferred until the app is complete enough to share with other users.
Not actively being built yet.

The database schema already includes `user_id` on all relevant tables. The companion role introduced
in Appendix A.10 (`owner`/`companion` with `linked_owner_id`) is a deliberate precursor. The full
multi-user design should evaluate compatibility with the companion model and plan the migration
path. When the time comes, the work is primarily:

- Registration UI and flow (note: `REGISTRATION_ENABLED` toggle already exists, added in Appendix
  A.5).
- Ensuring all queries filter by `user_id` (audit needed; substantially advanced by Section 1's
  access-control consistency phase).
- Role/permission model (parent vs. kid account).
- Kid account restrictions (view-only? limited editing?).
- **Account sharing model:** Some accounts may need to be visible to multiple users (e.g., a joint
  checking account shared between spouses, a savings account visible to both parent and child). The
  multi-user design should not assume strictly siloed data. A sharing model where specific accounts
  can be linked to multiple users (with configurable permissions: view-only vs. full access) would
  support household financial management. This does not need to be designed now but should be noted
  as a constraint so the eventual implementation does not paint itself into a
  single-user-per-account corner.

This section will be scoped when it becomes relevant.

---

## 7. Credit Card Accounts

**Status:** Plan of record APPROVED July 19, 2026; not started -- gated on the ratified sequencing
below. **Canonical plan:** `docs/plans/implementation_plan_credit_card.md` (the arc's live ledger;
this roadmap entry is a status pointer only, following the Section 1 pattern). The section is
numbered 7 because Sections 3-6 predate it; in execution order it runs ahead of them (see the
execution-order table).

**Goal:** Replace the phantom-payback Credit workflow with a real revolving credit-card account.
Today, marking an expense "Credit" flips it to a balance-excluded status and auto-creates a phantom
"CC Payback" expense in the next period, while the real card's debt, interest, due dates, and cash
back are invisible to the app. After this arc: marking a purchase Credit MOVES the transaction to
the card account and settles it there with its real category and period (the debt exists on the
books, spending attribution is preserved, net worth stops being overstated); paying the card is a
real checking -> card transfer, auto-maintained once per statement on the actual due date with the
amount derived live from the statement balance; statement cycles, grace, finance charges (APR/365 x
average daily balance), and flat-rate cash back with manual and auto-threshold redemptions all
become modeled, derived quantities. Live Credit pairs are migrated in the Alembic migration; settled
history is frozen; the old workflow code is deleted.

**Phases (per the plan; land into `dev` per phase):**

- **Phase 0 (CC0a-CC0c):** a revolving-credit account kind, the `budget.credit_card_params`
  satellite table, and the params setup flow (params-absent means dormant plain liability).
- **Phase 1 (CC1a-CC1c):** the revolving balance fold as an event stream, dispatched through the
  `balance_at` seam (consumes the shared instant-partition fold core that X1 lands).
- **Phase 2 (CC2a-CC2c):** pure statement math -- cycle windows, due dates, grace, minimum payment,
  the finance charge folded over the daily balance, and card APR on `rate_history`.
- **Phase 3 (CC3a-CC3d):** the charge-to-card action, the breaking mark-credit cutover with the
  live-pair migration, envelope split tender, and guard rails (a card refuses what it cannot model).
- **Phase 4 (CC4a-CC4c):** the derived statement payment (`card_payment_settings`, live amount
  overrides beside the loan's), with an underpayment warning and a projected finance charge.
- **Phase 5 (CC5a-CC5b):** rewards -- accrual as a derived figure; redemptions as real events,
  manual or at an auto-redeem threshold.
- **Phase 6 (outline):** the card cockpit page, grid affordances, and savings-cockpit tile states,
  via a later shekel-design loop.

**Locked developer rulings (July 19, 2026; recorded in the plan, do not reopen):** the re-account
model; the full derived statement cycle; one derived statement payment per cycle on the due date;
flat-rate cash back with redemption events; freeze-history/migrate-live-rows; zero-card owners get a
$0 account at migration time; the semantic renames (`is_credit` -> `is_card_tender` and kin) ship
in-arc at CC3c; APR/365 x average-daily-balance finance-charge math. Grid and companion hard
requirements: a charged expense stays visible on the source grid as a display-only ghost row; the
derived payment renders and deducts on the checking grid in its due-date period; the companion flow
survives end to end, pinned by test.

**Sequencing:** see the near-term execution order in the Overview -- balance-arc tail (Phase D; C9
already shipped) first (the fence is frozen until D3 and this arc needs new fence classifications;
D2's typed balances mean the revolving producer is born into the final structure), then the interim
prod ship, then X1, then Phases 0-5, then balance-arc X2-X4/E1/F1, with Phase 6 any time after Phase
5.

---

## 8. Import and Reconciliation

**Status:** Planned; ratified July 19, 2026. Runs after Section 7 (credit cards) and before Section
3 -- imported actuals feed every smart feature. Numbered 8 because Sections 3-6 predate it; the
execution-order table is the priority source. Write the plan of record (the Section 7 pattern) when
the arc starts.

**Goal:** end manual transaction entry as the default, and turn reconciliation from data entry into
verification. The developer's bank exports CSV with transactions, daily balances, and a running
balance; import drives the app's existing flows rather than adding new financial logic.

**Shape (staged; conservative by default):**

1. **One adapter, not an engine.** Parse the developer's bank CSV behind a thin interface; a second
   bank someday is a second small adapter. No universal-format machinery.
2. **Provenance and dedup.** New `import_batches` / `imported_rows` tables; composite-key dedup so
   re-uploading an overlapping window never double-imports. Additive schema only -- no surgery on
   core tables.
3. **Conservative matching + review.** Match imported rows to projected transactions by amount
   tolerance, date window, and name similarity. Everything lands in a review UI; nothing
   auto-settles until trust is earned (auto-apply thresholds are a later knob).
4. **Apply through existing seams.** A confirmed match marks the projected transaction paid with the
   actual amount through the same fenced status/settle chokepoints every other writer uses (the
   W9907 checker enforces this); an unmatched row can create a transaction or be ignored (transfer
   noise).
5. **Running-balance assertions.** The CSV's balance column vs `balance_at` per day: continuous
   anchor verification, surfacing drift the moment it appears instead of at the monthly true-up.
6. **Automation as a bolt-on, later.** If the manual download-and-upload ritual gets old, a
   SimpleFIN Bridge fetcher (token-based, read-only aggregator access; no bank credentials in the
   app, no money movement possible) slots in front of the identical pipeline. Decision deferred
   until the file-based path has proven the matcher.

**Interactions:** accelerates 3.1 auto-learn, 3.2, 3.9, and 3.12 by accumulating actuals; the
balance assertions extend the Section 2 reconciliation story to the cash side's daily grain.

---

## 9. Operations

**Status:** Standing track, not sequenced -- these interleave with feature work. Added July 19, 2026
so the operational work that protects the data is visible on the roadmap instead of living only in
the operator's head.

- **Argon2id password-hash migration** (Section 1 Phase 8 orphan, F-088/F-141): opportunistic
  on-login rehash from bcrypt. Land before any friends-and-family registration is ever enabled.
- **Config-drift check script** (Section 1 Phase 8 orphan): detect production config drifting from
  the hardened baseline.
- **Backup restore drill:** Postgres dumps and restic exist; what is untested is restore. A
  periodic, documented drill (restore the latest dump into a scratch container, run the
  reconciliation oracles against it) turns backups from hope into evidence.
- **Production monitoring:** container health, disk, certificate/tunnel expiry, and a failed-deploy
  signal beyond "the operator notices." ntfy (Section 4) is the natural sink.
- **Dependency and Postgres cadence:** pinned requirements need a periodic review rhythm; PG18 now,
  plan the next major before it is forced.
- **flask-login utcnow bump:** BLOCKED upstream; check the upstream release occasionally and land
  the bump when it ships.
- **Phase 10 host hardening (C-56):** remains operator-tracked (Section 1); listed here for
  visibility.

---

## Appendix A -- Completed Work

This appendix records work that has shipped. Each entry preserves its original v4-6 section label so
prior commits, PRs, and design docs continue to resolve. Full historical detail remains in
`project_roadmap_v4-6.md`; this appendix is a navigation index, not a duplicate.

### A.1 Critical Bug Fixes (v4-6 Section 3) -- COMPLETE March 2026

10 production bug fixes including the tax-on-gross-pay calculation error (3.1), recurrence
correctness audit (3.2), net biweekly mismatch (3.3), HTMX form re-render bugs (3.4, 3.7),
escrow-with-inflation entry (3.6), pension date validation (3.8), stale retirement message (3.9),
and the paycheck calibration feature (3.10) which superseded the originally-planned Actual Paycheck
Value Entry. See v4-6 Section 3 for full resolution notes.

### A.2 Transfer Architecture Rework (v4-6 Section 3A) -- COMPLETE March 2026

Eliminated the dual-path `budget.transactions` / `budget.transfers` architecture. Every transfer now
has two linked shadow transactions (one expense, one income); the balance calculator queries only
`budget.transactions`. Established the 5 transfer invariants that appear in CLAUDE.md. Supporting
documents: `docs/transfer_rework_design.md`, `docs/transfer_rework_inventory.md`,
`docs/transfer_rework_implementation.md`. See v4-6 Section 3A for full detail.

### A.3 UX/Grid Overhaul (v4-6 Section 4) -- COMPLETE March 2026

17 daily-use grid and detail-page improvements: full row headers (4.1), date format cleanup (4.3),
status refactor and rename to ID-based lookups across all reference tables (4.4a/b/c), tax config
page reorganization (4.6), post-creation parameter setup redirects (4.7, 4.8), chart contrast and
sizing fixes (4.9, 4.10), salary profile button placement (4.11), grid tooltip enhancement (4.12),
emergency fund coverage calculation fix (4.13), checking balance projection on the account detail
page (4.14), auto loan parameter fixes (4.15), retirement date validation UX (4.16), retirement
return rate clarity (4.17). Supporting document: `docs/implementation_plan_section4.md`. See v4-6
Section 4 for full detail.

### A.4 Account Parameter Architecture (v4-6 Section 4A) -- COMPLETE March 2026

Architectural rework that completed the metadata-driven account parameter dispatch system.
`HysaParams` renamed to `InterestParams`; `has_interest`, `has_amortization`, `has_parameters`,
`category_id`, and `max_term_months` flags drive all dispatch with zero hardcoded type ID checks.
Money Market and CD types enabled. Supporting document: `docs/account_parameter_architecture.md`.
See v4-6 Section 4A for full detail.

### A.5 Adversarial Audit Remediation (v4-6 Section 4B) -- COMPLETE March 2026

Comprehensive 17,844-line adversarial codebase audit identifying 1 Critical, 11 High, 17 Medium, and
15 Low findings. Critical: silent paycheck fallback in the recurrence engine (broad
`except Exception` masking financial calculation failures). High findings included scenario_id IDOR,
salary route info leakage, AccountType mutation accessibility, grid subtotals using float
arithmetic, seed script crash on migrated databases, and systematic ref-table string-name
comparisons. All findings remediated. Supporting documents: `docs/adversarial_audit.md`,
`docs/implementation_plan_audit_remediation.md`. See v4-6 Section 4B for full detail.

> **Note:** A second, deeper security audit was conducted in April 2026
> (`shekel-audits/security-2026-04-15/`). That audit's remediation work is tracked as Section 1 in
> this roadmap.

### A.6 Cleanup Sprint (v4-6 Section 5A) -- COMPLETE April 2026

Five tasks from production feedback: estimated-vs-actual grid calculation fix that introduced
`effective_amount` semantics (5A.1), category item sub-headers in grid (5A.2), salary listing page
button cleanup (5A.3), category management overhaul with edit, re-parent, and group dropdown (5A.4),
unified two-step delete/archive lifecycle pattern across templates, transfers, accounts, and
categories (5A.5). Source: `fixes_improvements.md`. See v4-6 Section 5A for full detail.

### A.7 Debt and Account Improvements (v4-6 Section 5) -- COMPLETE April 2026

16 tasks completing the debt account story: payment linkage to amortization engine (5.1),
income-relative savings goals with `ref.goal_modes` and `ref.income_units` (5.4), payoff calculator
multi-scenario visualization with original/committed/what-if lines and floor marker (5.5), savings
dashboard SRP refactor (5.6), ARM rate adjustment support in amortization engine (5.7), amortization
engine edge cases for overpayment and zero-balance termination (5.8), loan payoff lifecycle with
recurring transfer end date and account archival (5.9), refinance what-if calculator (5.10), debt
snowball/avalanche cross-account strategy (5.11), debt summary metrics and DTI ratio (5.12), full
amortization schedule view (5.13), payment allocation breakdown on loan dashboard (5.14), savings
goal progress trajectory (5.15), recurring obligation summary page (5.16). Tasks 5.2 (recurrence
audit) and 5.3 (actual paycheck value entry) were removed as superseded by Sections 3.2 and 3.10.
See v4-6 Section 5 for full detail.

### A.8 Mobile Responsiveness (Unplanned) -- COMPLETE April 2026

Unplanned work delivered in April 2026: CSS/JS/template-only changes for a mobile-responsive web
experience, including bottom-sheet patterns for transaction detail, single-period grid navigation,
and responsive layouts at Bootstrap `sm` and `md` breakpoints. No data model or service changes.

### A.9 Visualization and Reporting Overhaul (v4-6 Section 8) -- COMPLETE May 2026

Replaced the existing `/charts` page with two major additions: a summary dashboard at `/` (now the
app's landing page) and an analytics page at `/analytics` (tabbed container with calendar, year-end
summary, budget variance, and spending trends). Built the computation engines AND display layers for
budget variance analysis (spec'd at Section 3.5), annual expense calendar with third-paycheck month
detection (Section 3.6), and spending trend detection (Section 3.7). Added CSV export for all
analytics views (analytics-level CSV; full transaction-level export remains in Section 5). Fixed the
x-axis date format bug (8.0a). Task 8.0b (inaccurate balance values) excluded per the scope
document; no bug found in code audit. Supporting documents: `docs/section8_scope.md`,
`docs/implementation_plan_section8.md`. See v4-6 Section 8 for full detail.

### A.10 Spending Tracker and Companion View (v4-6 Section 9) -- COMPLETE May 2026

Three interconnected features: sub-transaction entry tracking via `budget.transaction_entries` on
budget-type transactions with remaining balance visibility (9.1, 9.2, 9.4); entry-level credit card
workflow with aggregated CC paybacks per parent transaction per period (9.3); companion user role on
`auth.users` (`role` and `linked_owner_id` columns) with mobile-first single-period view, entry
CRUD, and mark-as-Paid capability (9.5). Balance calculator extended with the entry-aware effective
amount formula `checking_impact = max(estimated - sum_credit, sum_debit)` for mid-period mixed
debit/credit scenarios. Parent transactions with `track_individual_purchases` cannot use legacy
Credit status (entry-level credit replaces it). Supporting document:
`phase_scope_spending_tracker.md`. See v4-6 Section 9 for full detail.

### A.11 Carry-Forward Aftermath and Envelope View -- COMPLETE May 2026

Reworked what happens to a budget row's leftover when a pay period closes. Carry-forward now
branches by template kind (Option F): envelope templates settle the source row at real spend and
roll the leftover into the target period's canonical row, while discrete templates keep the
move-whole behaviour. Introduced the `is_envelope` column (renamed from
`track_individual_purchases`), a shared `settle_from_entries` helper, a pre-flight confirmation
modal, and Marshmallow validation that rejects `is_envelope=True` on income templates. The grid
display ("one row per envelope per period") shipped alongside the mobile v3 work (A.13). The earlier
`carried_from_period_id` FK proposal in `implementation_plan_envelope_view.md` was superseded by the
settle-and-roll model. Supporting docs: `docs/historical/carry-forward-aftermath-design.md`,
`docs/historical/carry-forward-aftermath-implementation-plan.md`,
`docs/historical/implementation_plan_envelope_view.md`.

### A.12 Test Performance and Per-Worker Databases -- COMPLETE May 2026

A multi-phase effort to make the ~5,500-test suite fast and reliable under `pytest-xdist`. Each
xdist worker now gets its own database cloned from a prebuilt template (`build_test_template.py`)
instead of sharing one database with per-test TRUNCATE; PostgreSQL was upgraded 16 -> 18 across the
test, CI, dev, and prod clusters; and two pre-existing test-isolation bugs (rate-limit cleanup and
malformed index teardown) were fixed, collapsing a 50-70% intermittent failure rate to deterministic
green. Result: the full suite runs in roughly 65 s at `-n 12`. Supporting docs:
`docs/audits/test_improvements/per-worker-database-plan.md`,
`docs/audits/test_improvements/test-performance-implementation-plan.md`,
`docs/audits/test_improvements/phase1-flake-investigation.md`.

### A.13 Mobile Experience v3 -- COMPLETE May 2026

A follow-up to the April 2026 mobile work (A.8) that rebuilt the mobile grid around daily workflows:
a "This Period" default tab with period navigation and a "Plan" multi-period accordion, per-card
inline action bars (Mark Paid / Edit Amount / Open Full), drag-to-dismiss bottom sheets with iOS
keyboard avoidance, a swipe-to-mark-paid gesture with a non-gesture equivalent,
`inputmode="decimal"` on monetary inputs, card layouts for the list pages, a navbar offcanvas
drawer, and a static-only service worker plus a PWA manifest with maskable icons. Desktop and mobile
transaction rendering were de-duplicated into shared macros. Supporting docs:
`docs/historical/implementation_plan_mobile_v3.md`, `docs/historical/mobile_follow_up.md`.

### A.14 Amortization Engine Split -- COMPLETE May-June 2026

Split `amortization_engine` from one fused function into two primitives (replay confirmed history,
project forward) plus a composer, which made a real bug syntactically impossible: the old code
applied `extra_monthly` to months with no confirmed payment, fabricating accelerated history. The
payoff-calculator chart (Original / Committed / Accelerated), the dashboard loan chart, and all
downstream consumers now read through the `loan_resolver` chokepoint. This refactor is the substrate
for Section 2.1's E-18 event-derived loan resolver and also resolved the C0302 (too-many-lines) root
by moving the engine into a package. Supporting docs:
`docs/plans/historical/2026-05-21-amortization-engine-split-implementation.md`,
`docs/plans/historical/2026-05-21-amortization-engine-split-replay-projection.md`.

### A.15 Homelab Security Audit -- COMPLETE May 2026

A companion infrastructure audit (distinct from the app-level Section 1) of the operator's homelab
Docker stack: Cloudflare Tunnel ingress, the shared Nginx vhost, real-IP pinning, security headers,
per-container hardening (`cap_drop`, `no-new-privileges`, `read_only`), and image digest pinning.
Six of ten findings were closed in a same-day remediation pass; the remainder (tier segmentation, a
UniFi upgrade, a Cloudflare Access scope) were deferred. It shares the C-33 network-isolation work
with Section 1. Supporting doc: `shekel-audits/homelab-security-2026-05-09/findings.md`.

### A.16 Code Quality Audit and Remediation -- COMPLETE June 2026

A three-part internal quality program over `app/` and `scripts/`. (1) Both trees were driven to a
hard pylint 10.00/10 floor, CI-locked, with custom Shekel checkers (decimal-from-float,
refname-compare, bare-money-quantize, disable-rationale) and several package splits (validation,
auth, loan, amortization, transactions, transfers) to fix too-many-args and duplicate-code at the
root. (2) A design-quality second pass reviewed every file the cleanup touched against a right-
abstraction / DRY / SOLID / Pythonic / test-quality rubric, confirming no over-reach or
gold-plating. (3) A deep adversarial hunt surfaced 88 verified findings (6 High, 34 Medium, 48 Low),
now exhausted, including correctness fixes (a delete-source payback-cleanup orphan and a
transfer-specific transition map). Supporting docs: `docs/audits/pylint-cleanup/plan.md`,
`docs/audits/pylint-cleanup/quality-pass.md`, `docs/audits/pylint-cleanup/deep-quality-hunt.md`.

### A.17 Polyglot Standards Cleanup -- COMPLETE June 2026

A standards audit of every non-Python surface (shell, JavaScript, CSS, Jinja templates, SQL, Docker,
CI workflows, Markdown): 124 findings, of which 113 were judgment-only with no mechanical linter to
catch them. Notable fixes included a restore script that dropped the database before validating its
input, a Ctrl+C path that could leave transactions half-mutated, and a fail-open hook cluster. All
five phases shipped, and a stack of non-Python linter floors (Biome, shellcheck/shfmt, djlint,
actionlint/zizmor, hadolint, yamllint, gitleaks, typos, rumdl) was wired into pre-commit and CI.
Shipped across PRs #33-#36. Supporting docs: `docs/audits/polyglot-cleanup/findings.md`,
`docs/audits/polyglot-cleanup/tooling.md`.

### A.18 Dev/Prod Container Parity -- COMPLETE June 2026

An audit comparing the production container stack against the development setup, closing the gaps
that mattered after stabilization: a dev app healthcheck, scoped Flask debug binds, a dev Redis with
rate limiting, owner-role parity by making the containerized dev app the primary workflow, Postgres
digest pinning, an external pgdata volume, and host hygiene. The biggest correctness item was
pinning `TZ=America/New_York` on the app services so date-boundary logic matches production (see
`project_timezone_display_policy`). One operator action (a systemd audit-cleanup timer) is the only
follow-up requiring host sudo. Supporting doc: `shekel-audits/dev-prod-parity/findings.md`.

### A.19 Fable 5 UI/UX Overhaul -- Core shipped June 2026 (per-screen rollout ongoing)

A full UI/UX overhaul (not a reskin) under a new design language. Shipped pieces: the grid rebuild
(month-spine layout, an anchored action card replacing the three-tier edit system, a Ctrl+K command
palette, keyboard cursor actions, and targeted HTMX swaps replacing full-page reloads); the Steel
Ink token/theme system app-wide; the `app.css` split into seven concern-scoped stylesheets plus a
per-render chart factory and content-hash static-asset versioning; the dashboard "Terminal Road"
rebuild (a chart-led health check driven by pulse/tracks producers); and app-wide standardization of
currency formatting (the money macro and `ShekelChart.formatMoney`, fixing `$-270` -> `-$270`) and
timezone display (UTC in the database, America/New_York at presentation). Grid, theme, and the CSS
split shipped via PRs #31-#32; the dashboard rebuild followed on `dev`. Still ongoing: the per-user
theme-selector UI (Scope B) and the per-screen rollout to the remaining pages. Supporting docs in
`docs/design/`: `fable5-design-language.md`, `grid_audit.md`, `css_architecture_audit.md`,
`dashboard_card_audit.md`, `overhaul_plan.md`, `visual_loop.md`.

### A.20 Pay-Period CRUD and Rolling Window -- COMPLETE June 2026

New feature (not previously on the roadmap): full lifecycle management of the pay-period schedule.
Phase 0 hardened the schema (deferrable anchor FK, uniqueness constraint). Phase 1 added a
`budget.pay_schedule` table, a lock classifier, and the extend / truncate / regenerate operations
with block-and-confirm gates on destructive actions. Phase 2 added a continuous rolling window that
keeps N periods ahead of today via a Postgres advisory lock and an idempotent upsert, with grid and
dashboard trigger hooks and a settings UI. Phase 3 added a bounded full reset behind a zero-settled
gate that wipes the schedule (including the anchor period) and re-anchors via a schema-qualified
`SET CONSTRAINTS ... DEFERRED`, re-phasing the recurrence offset. Shipped via PR #37. Supporting
doc: `docs/plans/historical/implementation_plan_pay_period_crud.md`.

### A.21 Balance Architecture Program (Stage B built) -- Levels 1-2 SHIPPED July 2026

The reversal and build-out of Section 2.2: the `balance_at` seam with the W9906 fence checker and
per-kind cross-page oracle (PR #45, prod June 27), kind-correct grid/obligations balances (PR #47),
and the append-only double-entry posting ledger grown one domain at a time with backfill migrations
and reconciliation oracles -- transfers (Step 2, PR #48), cash and envelope transactions (Step 3,
PRs #49/#50), loan real-splits with effective-dated escrow (Step 4, PR #51), the loan read switch
(PR #52), and account anchors plus the confirmed-ledger income statement and balance sheet (Step 5,
PR #58) -- hardened by the July 2 adversarial review remediation (R1-R10, PR #54). The follow-on
fail-loud ledger-authority arc (the loan balance as a total fold over an event stream; derived
payoff) is complete through C9 on `dev`; its open tail is tracked in Section 2.2, not here.
Canonical doc: `docs/audits/balance_architecture/README.md` (the only live doc for the arc; 25
predecessor planning docs are archived beside it).

### A.22 Escrow Configuration Redesign and Loan-Date Correctness -- COMPLETE July 2026

Escrow lines gained identity and time: supersession tables with effective dates (expand, reader
cutover, legacy-table drop), date-aware loan-payment cash with capture-on-settle, a
`loan_payment_settings` table replacing `derive_from_loan` with standing extra principal flowing
into both live payment cash and the payoff projection, domain-dated escrow inflation, a merge tool
to reunify a split line, and the plan-aware forward trajectory at the resolver seam, shipped on July
7 via PR #60. Alongside it, the loan-detail mortgage fixes (rename-duplicate escrow collapse,
tracking-start opening for mid-life-imported loans) and the due-date identity fix (a loan payment is
dated by its own due date, not its pay period). Spec:
`docs/design/escrow_line_identity_refactor.md`.

### A.23 Analytics and Taxes Overhaul -- COMPLETE July 2026

`/analytics` rebuilt as four slices under a four-pill shell (Calendar, Spending, Statements, Taxes)
with unified basis chips and real tab URLs: the Calendar month cockpit with a daily running-balance
data layer and ledger day cells; the Taxes tab, backed by a new annual filing-time tax liability
engine, YTD withholding checkpoints, and a tax report producer (refund hero, derivation ledger,
hybrid W-2 preview, Schedule A, refundable ACTC and NC filing extensions); the Spending months-lead
cockpit (D7) on a unified spending producer; and the Statements restructure presenting Step 5's
confirmed-ledger income statement and balance sheet with CSV export.

### A.24 Fable 5 Per-Screen Rollout -- COMPLETE July 2026

Completion of the rollout A.19 started, all screens developer-accepted: the Accounts cockpit
(`/savings` + `/accounts` unified, conditional sparklines, group cells, the P-AC1 net-worth
diverging stream, and the rebuilt date-anchored property equity chart with the three-tier debt
line); Account detail unified into one cash-detail band page (PR #55); the Retirement rebuild to
direction D with readiness producer, lever solvers, and merit-raise horizon (PR #56); the Salary
cockpit (PR #57); the Recurring surface unifying templates, transfers, and obligations with a
recurrence-conflict chooser and shared form cards; Loan detail in band grammar with measured
producers (chips, band chart, overlay); Investment detail in band grammar; and the app-wide navbar
and brand (Steel Ink ink band, silver coin marks, Besley 700 wordmark).

### A.25 UI/UX Polish Pass -- COMPLETE July 2026

A cross-page polish pass over the rebuilt app (register: `docs/design/ui_ux_polish_audit.md`; plans:
`docs/plans/historical/implementation_plan_ui_ux_polish.md`,
`docs/plans/historical/implementation_plan_ui_closeout.md`). Waves 0-2 fixed the screenshot
instrument, the WCAG AA token skins, and the grid column contract; sessions S1-S16 delivered the
credit-violet token split, the Graphite dark ladder, the Besley wordmark, the D12 chart ramp policy,
and the dashboard, grid, accounts, spending, statements, and settings restructures; the closeout
sessions added designed error fragments, status pre-hints, the quick-create name field, app-wide
breadcrumb removal with back buttons, the D14 click-to-edit anchor idiom, and PWA manifest
versioning. Shipped to production through PRs #61-#63 (July 7-13). Residue is listed in the UI/UX
section.

### A.26 Test Isolation and Tooling Hardening -- COMPLETE July 2026

Container-spawning deploy tests moved behind a docker marker with a fail-closed guard so bare pytest
cannot touch the production daemon; a persistent Playwright auth-state helper for visual
verification; `IDLE_TIMEOUT_MINUTES` pinned in TestConfig against environment drift; the
`shekel_checkers` pylint plugin split into a package under its own CI-locked 10.00/10 floor; and new
fence checkers W9907 (transaction-status bypass), W9908 (ledger-model import), and W9909
(unclassified fenced export). The suite grew from ~5,500 to ~7,400 tests over the period.

---

## Appendix B -- Deferred Items Reference

| Item                                  | Deferred From             | Notes                                                                  |
| ------------------------------------- | ------------------------- | ---------------------------------------------------------------------- |
| Scenarios (named, clone, compare)     | v3 Phase 7                | Indefinitely deferred; effort not worth reward                         |
| Paycheck calibration                  | fixes_improvements.md     | Completed as Appendix A.1 task 3.10                                    |
| Fluctuating/seasonal bills            | fixes_improvements.md     | Addressed by Section 3 task 3.1 (seasonal forecasting, planned)        |
| Multi-user / kid accounts             | v2 Phase 6                | Section 6; on the table, deferred until ready to share; schema ready   |
| Checking account APY/interest         | fixes_improvements.md     | User confirmed checking APY is negligible; not implementing            |
| Recurrence pattern audit              | Roadmap v4, task 5.2      | Removed; section 3.2 confirmed all patterns are correct                |
| Actual paycheck value entry           | Roadmap v4, task 5.3      | Removed; superseded by paycheck calibration (Appendix A.1 task 3.10)   |
| implementation_plan_section5.md       | Roadmap v4.1, Section 5   | Defunct; Section 5 completed April 2026 without this plan (a new implementation plan was written from scratch). |
| CSV export                            | v2 Phase 6                | Listed in v2 Phase 6 (Hardening & Ops) but not implemented. Moved to Section 5 task 5.1. |
| Account Types editing                 | fixes_improvements.md     | Completed as Appendix A.4 settings UI enhancement (edit path added with metadata flags) |
| Salary button duplication             | fixes_improvements.md     | Completed: Appendix A.3 task 4.11 (`/salary/{id}/edit` fixed March 2026); `/salary` page fix completed as Appendix A.6 task 5A.3 (April 2026) |
| Grid estimated vs. actual             | fixes_improvements.md     | Completed as Appendix A.6 task 5A.1 (April 2026)                       |
| Grid transaction sort display         | fixes_improvements.md     | Completed as Appendix A.6 task 5A.2 (April 2026)                       |
| Category editing and add flow         | fixes_improvements.md     | Completed as Appendix A.6 task 5A.4 (April 2026)                       |
| CRUD deactivate/delete inconsistency  | fixes_improvements.md     | Completed as Appendix A.6 task 5A.5 (April 2026)                       |
| Charts: x-axis date format            | fixes_improvements.md     | Completed as Appendix A.9 task 8.0a                                    |
| Charts: inaccurate values             | fixes_improvements.md     | Investigated as Appendix A.9 task 8.0b; no bug found in code audit; excluded |
| Charts: total overhaul                | fixes_improvements.md     | Completed as Appendix A.9 (full visualization and reporting overhaul)  |
| Double-entry ledger (Stage B)         | Roadmap v5 Section 2.2     | Deferral REVERSED June 26, 2026: the revisit valve fired (balance divergence reappeared). Built as the posting ledger program; see Section 2.2 and Appendix A.21 |
| Envelope budgeting (Stage C)          | Roadmap v5 Section 2.3     | Deferred pending confirmed need; standalone-capable, does not require Stage B            |
| Bill due-date optimization (3.10)     | Roadmap v5.4 (was Section 3) | Demoted July 19, 2026: low-balance alerts (detection) and the period-move UI (action) already exist; only the heavy recommender was missing. Revisit if low-balance alerts fire regularly and choosing which bill to move is ever non-obvious |
| Deduction inflation (3.4)             | Roadmap v5.4 (was Section 3) | Folded into 3.3 as a yearly open-enrollment prompt; not a separate engine |
| PDF export (5.2)                      | Roadmap v5.4 (was Section 5) | Replaced by print stylesheets on the statement/loan/year-end pages |
| Email notification delivery (4.4)     | Roadmap v5.4 (was Section 4) | Replaced by self-hosted push (ntfy/Gotify); no mail server to run or secure |
| Notification types beyond the core 5  | Roadmap v5.4 (was Section 4) | Deferred until the daily-check engine + five core types prove out; the full 15-type catalog is in git history (v5.3) |

---

## Change Log

| Version | Date       | Changes |
| ------- | ---------- | ------- |
| 4.0     | 2026-03-24 | Post-production roadmap: added critical bug fix sprint, UX/grid overhaul phase, recurring transaction improvements phase; rescoped Phase 9 with seasonal expense forecasting; rescoped Phase 10 with tiered notification system; added multi-user as far-future placeholder; established priority ordering based on production usage feedback. |
| 4.0.1   | 2026-03-24 | Hosting updated to Arch Linux desktop with Docker/Nginx/Cloudflare Tunnel; paycheck calibration feature added as section 3.10; seasonal history data model updated with billing period dates indexed by consumption period midpoint. |
| 4.1     | 2026-03-27 | Section 3 marked complete. Section 4 expanded with production feedback (tasks 4.11-4.17). Section 5 retitled to "Debt and Account Improvements" with task 5.1 expanded to all debt types and tasks 5.2/5.3 removed. |
| 4.2     | 2026-03-30 | Sections 3A, 4, 4A, 4B marked complete. Section 5 expanded with seven new tasks (5.6-5.12) and four more (5.13-5.16). Section 6 expanded with 3.5-3.7. Section 7 notification types expanded. New Section 8 added (Dashboard, Reporting, and Data Management) with subsections 8.1-8.4. |
| 4.3     | 2026-03-31 | New Section 5A (Cleanup Sprint, five tasks). Section 8 retitled to "Visualization and Reporting Overhaul" with chart bug fixes 8.0a/8.0b prerequisite. Task 8.4 separated into Section 8A (Data Export). Phase ordering updated. |
| 4.4     | 2026-04-07 | Sections 5A and 5 marked complete. Mobile responsiveness added as completed unplanned work. Section 8 marked in progress; priority moved 6 -> 4. Five new tasks added to Phase 9: 3.8-3.12. |
| 4.5     | 2026-04-07 | Section 7 fully rescoped: notification types expanded from 7 to 15 across 6 named groups; data model expanded with explicit columns for all configurable parameters; new infrastructure subsections (snooze, auto-resolve, persist dashboard alerts); in-app delivery split into bell/dropdown and full page; settings UI Option B grouped expandable sections; email delivery expanded with delivery window and batching. |
| 4.6     | 2026-04-09 | New Section 9 added (Spending Tracker and Companion View): sub-transaction entry tracking, entry-level credit card workflow, companion user role, balance calculator extended with entry-aware effective amount. Multi-user (Section 10) bumped from Priority 8 to Priority 9. Sections 10-12 renumbered. |
| 5.0     | 2026-05-06 | Major reorganisation. Numbering: collapsed the dual Priority/Section system into a single sequential top-level track in execution order. Subsection numbers renumbered to follow their new parent: former 6.x became 3.x (Smart Features), former 7.x became 4.x (Notifications), former 8A.x became 5.x (Data Export). Internal cross-references updated to match. Completions: marked Visualization and Reporting Overhaul (was Priority 4 / Section 8, now Appendix A.9) and Spending Tracker and Companion View (was Priority 8 / Section 9, now Appendix A.10) complete (May 2026). Completed work relocated to Appendix A with original v4-6 section labels preserved on the appendix headings for cross-reference with prior commits and design docs. New Section 1 (Security Remediation) added, linking to `docs/audits/security-2026-04-15/remediation-plan.md` (in progress, 16 of 56 commits merged). New Section 2 (Financial Calculation Consistency) added with three sequenced stages (Stage A committed; Stages B and C decision-pending). Execution order for remaining work: Security, Financial Consistency, Smart Features, Notifications, Data Export, Multi-User. Phase 10 (was Section 7) became Section 4. Section 8A (Data Export) became Section 5. Section 10 (Multi-User) became Section 6. Section 11 (Deferred Items Reference) became Appendix B. Supersedes `project_roadmap_v4-6.md` (preserved as historical archive). |
| 5.1     | 2026-06-14 | Realigned with shipped code. Section 1 (Security) re-scored against merged git history: Phases 1-7 complete, Phase 8 low/info findings consolidated into C-40/C-44/C-45/C-46 and largely shipped, Argon2id migration + config-drift script remain, Phase 9 (C-53..C-55) and C-39 not pursued (rationale recorded in 1.3), Phase 10 operator-tracked. Section 2 (Financial Calculation Consistency): Stage A marked COMPLETE (25-finding audit remediated; canonical producers E-18/E-19/E-25/E-26/E-27; HIGH-01 cross-page regression lock), Stages B and C deferred with revisit criteria. Appendix A extended with A.11-A.20 for work completed since v5.0: carry-forward aftermath and envelope view, test performance and per-worker databases, mobile v3, amortization engine split, homelab security audit, code quality audit and remediation (pylint 10.00/10 + quality-pass + deep-quality-hunt), polyglot standards cleanup, dev/prod container parity, the Fable 5 UI/UX overhaul, and pay-period CRUD with a rolling window. Folded in the still-valid decisions from the unmerged v5.1 draft on branch `claude/busy-thompson-rEhgG`. Future Sections 3-6 (Smart Features, Notifications, Data Export, Multi-User) unchanged. |
| 5.2     | 2026-06-25 | Re-prioritised post-overhaul work after a fresh ROI review grounded in the current code, and surfaced the in-progress UI/UX overhaul. UI/UX: the Fable 5 per-screen rebuild was added to the execution order and a new in-progress section (Grid and Dashboard complete, Accounts in progress, remaining screens planned); Appendix A.19 retained for the shipped core. Section 2: Stage B (double-entry) deferred indefinitely with the revisit valve retained, as the highest-effort, highest-risk item for no user-facing gain while Stage A's canonical producers and HIGH-01 lock already hold balances correct; Stage C (envelopes) dependency on Stage B corrected (the entry-aware math already runs on budget.transactions via E-25 and Stage C is a single additive table, so it is standalone-capable) and deferred pending confirmed need. Execution order after the overhaul: Section 3 Smart Features next, then Section 4 Notifications (so alerts can build on Smart Features), then Section 5 Data Export (low priority, CSV first), then Section 6 Multi-User (on the table, deferred until ready to share). No future-work specs deleted; status and priority changes only. |
| 5.3     | 2026-07-19 | Recorded the Stage B reversal and build-out: v5.2's double-entry deferral was reversed on June 26 when the revisit valve fired (balance divergence reappeared, PRs #43/#44) and the Option D investigation found the defect class architectural. Section 2.2 rewritten from DEFERRED INDEFINITELY to REVERSED AND BUILT: the balance_at seam (PR #45) with the W9906 fence, kind-correct balances (PR #47), the posting ledger Steps 2-5 (PRs #48-#52, #58), the July 2 adversarial review remediation (PR #54), and the in-progress fail-loud ledger-authority arc (Phases A/B and C1-C9 complete on dev; open tail = Phase D, F3 prod ship, X1-X4, E1, F1). UI/UX overhaul marked COMPLETE: all screens rebuilt and accepted (accounts cockpit, account detail PR #55, retirement PR #56, salary PR #57, analytics, recurring, loan, investment, navbar/brand, settings) plus the polish pass (Waves 0-2, S1-S16); shipped through PR #63 (July 13); residue listed. New Section 7 (Credit Card Accounts) added from the plan of record approved 2026-07-19, with its eight locked rulings and the ratified cross-arc sequencing (Phase D -> interim prod ship -> X1 -> CC Phases 0-5 -> X2-X4/E1/F1 -> CC Phase 6; C9 shipped in-arc July 19); execution-order table and Section 3's start gate updated to match. Appendix A extended with A.21-A.26 (balance architecture program, escrow configuration redesign + loan-date correctness, analytics/taxes overhaul, per-screen rollout completion, UI/UX polish pass, test-isolation and tooling hardening). Appendix B Stage B row updated. No future-work specs deleted. |
| 5.4     | 2026-07-19 | Slimming revision (developer-ratified pruning pass). Sections 3-5 shrunk from full specifications to intent + priority + pointer; the April 2026 spec text is superseded and preserved in git history at v5.3 (commit bb879797) -- the first revision to deliberately remove spec text. Pruned: 3.4 folded into 3.3; 3.10 demoted to Appendix B with a revisit trigger; 5.2 replaced by print stylesheets; 4.4 email delivery replaced by self-hosted push (ntfy). Notifications rescoped to the daily-check engine + five core types (low balance, missed payment, period aging, large expense, period-close digest), the weekly digest re-founded as a period-close digest. Smart features re-ranked (3.2 -> 3.12 -> 3.9 -> 3.1-as-auto-learn; manual seasonal history grid dropped) with five recorded candidates (tax-withholding drift, safe-to-spend, price-creep watchdog, period-close digest, seasonal auto-learn). New Section 8 (Import and Reconciliation): one-bank CSV adapter, provenance + dedup tables, conservative match + review driving the existing settle seams, running-balance assertions vs the balance seam, SimpleFIN bolt-on deferred; slotted after Section 7, before Section 3. New Section 9 (Operations): Argon2id migration, config-drift script, restore drill, prod monitoring, dependency/Postgres cadence, blocked flask-login bump, C-56 pointer. Execution-order table and near-term list updated; Section 1.4 points its Phase 8 orphans at Section 9. Numbering stays chronological; a v6 reorganisation will renumber once the balance and credit-card arcs close. |
