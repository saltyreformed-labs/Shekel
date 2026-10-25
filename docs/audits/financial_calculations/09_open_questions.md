> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Open Questions

Questions surfaced during the audit that the developer must answer before the corresponding finding
can be classified. Phases write into this file as questions arise; later sessions consult it.

## Candidate behavioral expectations needing developer confirmation

(P0-b populates this section.)

Q-01: Is the canonical rounding rule for monetary calculations
`Decimal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)`?

Why it seemed natural to assume: the audit plan's inventory step (section 1.1) and required-grep
list (section 13) both name `ROUND_HALF_UP` and `quantize` as expected patterns to find in the code,
and the schema rule `NUMERIC(12,2)` fixes storage precision at two decimal places, so a single
rounding mode is strongly implied for any computation that produces a stored or displayed money
value.

Why no explicit source could be found:
`grep -inE "round_half|quantize|two decimal|2 decimal|cent" CLAUDE.md docs/coding-standards.md docs/testing-standards.md`
returns no matches; the standards documents fix the storage type but not the rounding mode used by
calculation code.

Question for the developer: should every monetary calculation that produces a stored or displayed
value quantize to two decimal places using `ROUND_HALF_UP`, and if so, do you want this added to
section 0.3 as a behavioral expectation (with citation to your answer) before P0-c starts the
plan-vs-code watchlist?

A-01 (developer, 2026-05-13): Yes. The canonical rounding rule for every monetary calculation that
produces a stored or displayed value is `Decimal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)`.
This rule is not currently documented in `CLAUDE.md`, `docs/coding-standards.md`, or
`docs/testing-standards.md`, but it is the established convention and the audit should treat it as a
behavioral expectation (Phase 0.3 addition). Any computation that produces a money value at a system
boundary (storage, display) without quantizing to two decimal places using ROUND_HALF_UP is a
finding.

A-01 verification (auditor, 2026-05-15): PARTIALLY ACCURATE. The rule IS the established convention
(109 `ROUND_HALF_UP` occurrences across 19 files; every service that names a rounding mode uses it;
rule absent from `CLAUDE.md` and the two standards documents as claimed). The "every monetary
boundary" framing is not literally true. Concrete violations:

- 24 monetary `.quantize()` calls omit `rounding=ROUND_HALF_UP` (defaults to `ROUND_HALF_EVEN`,
  banker's rounding):
  `app/services/investment_projection.py:93, 96, 159`;
  `app/services/savings_dashboard_service.py:266, 872, 873`;
  `app/services/retirement_dashboard_service.py:197, 211, 214, 240, 390`;
  `app/routes/investment.py:131, 223, 226, 319, 458, 535, 538, 580, 585, 586, 589, 670`;
  `app/routes/loan.py:968`.
- 1 intentional `ROUND_CEILING` on a money value: `app/services/savings_goal_service.py:462-463`
  in `_compute_required_monthly` so the user contributes "at least enough" (documented in the
  function's docstring at line 438).
- 3 Jinja templates do arithmetic on money (also violates `docs/coding-standards.md` "Templates
  are for display, not computation"): `app/templates/loan/_schedule.html:55`,
  `app/templates/loan/_payoff_results.html:72`, `app/templates/loan/_escrow_list.html:37`.
- 1 JS file does monetary arithmetic: `app/static/js/retirement_gap_chart.js:24-25`.
- No centralized `round_money()` helper exists; every service redeclares `TWO_PLACES =
  Decimal("0.01")` locally. This is the substrate for the drift.

Recommendation: either accept A-01 as the canonical rule (with the 24+5 violations as Phase 3
findings) or amend A-01 to acknowledge them explicitly so Phase 3 starts with a documented list. The
absence of a centralized money-rounding helper is a candidate Phase 6 DRY finding. See
`answer_verification.md` Section 1 (A-01) for full evidence.

A-01 resolution (developer, 2026-05-18): Accept A-01 as the canonical rule, refined and formalised
as E-26 (`00_priors.md` §0.3). Rule: full-precision Decimal intermediates; quantize 2dp
ROUND_HALF_UP ONLY at the storage/display boundary, via ONE centralized helper
(`app/utils/money.py`: `round_money(x)` default, explicitly-named sanctioned variants e.g.
`round_money_ceiling(x)`, no `rounding=` kwarg). The 24 bare-`.quantize()` banker's-rounding sites
are Phase-3 DIVERGE findings against E-26. The `savings_goal_service.py:462` ROUND_CEILING is a
SANCTIONED documented exception (re-expressed as `round_money_ceiling()`), explicitly NOT a
finding -- so A-01 verbatim ("no exceptions") is rejected as proven-incorrect. The template/JS
money-arithmetic sites are findings against the existing E-16/E-17, not re-litigated here. The
absence of a centralized helper plus the 19-file `TWO_PLACES` redeclaration is confirmed as the
Phase-6 DRY root cause; remediation is the single helper with named variants. Verification status
of A-01 moves from PARTIALLY ACCURATE (open) to RESOLVED via E-26. Consistent with the E-18
through E-25 single-source-of-truth family.

## Cross-plan contradictions to adjudicate

Questions surfaced by P0-c when comparing watchlist entries across plans. Each maps to a `C-NN`
entry in section 0.5 of `00_priors.md`. The developer answers; Phase 3 then has a single source of
truth to compare the code against.

Q-02 (maps to C-01): Carry-forward envelope semantics. The plans `carry_fwd_design` (Option F) and
`carry_fwd_impl` (its execution) settle the envelope source row in place (status DONE/RECEIVED,
`actual_amount = entries_sum`, `pay_period_id` unchanged) and bump the target canonical's estimate;
`envelope_view` keeps the source row moving (post-33cd21e behavior) and groups canonical plus
carried members at display time via a new `carried_from_period_id` column. Which architectural shape
is the current direction for envelope items: data-layer settle (Option F), display-layer envelope
view, or both layered together?

A-02 (developer, 2026-05-13): The data-layer settle (Option F / `carry_fwd_impl`) is current.
`envelope_view` is superseded: its display goal (a combined envelope cell, e.g. `18/135`) was
reached by Option F's bumped-canonical mechanism, so `envelope_view`'s data model and aggregation
helper were never built. Evidence:

- `app/services/carry_forward_service.py:275` branches on `template.is_envelope`; the envelope
  branch calls `transaction_service.settle_from_entries(source)`
  (`app/services/transaction_service.py:38-169`), which mutates status/paid_at/actual_amount only;
  `pay_period_id` is unchanged.
- Target canonical's `estimated_amount` is bumped by leftover and `is_override=True` is set
  (`app/services/carry_forward_service.py:891-894`).
- Combined display arises naturally: `app/templates/grid/_transaction_cell.html:43` renders
  `entry_sum / estimated_amount` for the single bumped canonical row.
- `envelope_view`'s additions are absent: zero `carried_from_period_id` matches across `app/` and
  `migrations/`; no `app/services/grid_aggregation.py`; no `EnvelopeCell` anywhere in the codebase.
- End-user observation (developer): the source row stays in the source period showing $65 with a
  Done badge, consistent with Option F and inconsistent with `envelope_view`.

A-02 verification (auditor, 2026-05-15): ACCURATE. All seven evidence bullets verified at the cited
locations. Two minor notes: `settle_from_entries` ends at `app/services/transaction_service.py:168`,
not 169 (the file is 168 lines); the render at `app/templates/grid/_transaction_cell.html:42-44` is
gated on `show_progress` at line 19, which requires `status_id == STATUS_PROJECTED` (true for the
bumped target canonical in the steady state, which is the case the developer is describing). Zero
matches for `carried_from_period_id`, `EnvelopeCell`, or `grid_aggregation*` anywhere in code or
migrations. See `answer_verification.md` Section 1 (A-02).

Q-03 (maps to C-02): Recurrence skip rule for `is_override` rows. `carry_fwd_impl` leans on the
existing rule that any `is_override=True` row blocks regeneration of its canonical; `envelope_view`
(sections 4.4 and 12) narrows the rule so carried-only overrides do NOT block generation, only
non-carried (manually-edited) overrides do. Should the recurrence and transfer-recurrence engines
treat carried overrides as non-blocking (envelope_view's narrowing) or continue blocking on any
override (carry_fwd_impl's assumption)?

A-03 (developer, 2026-05-13): Continue blocking on any `is_override=True` row. The recurrence and
transfer-recurrence engines still treat any `is_override=True` row as a skip signal
(`app/services/recurrence_engine.py:128`, `app/services/transfer_recurrence.py:97`).
`envelope_view`'s narrowing of the rule (carried-only overrides should not block) was never
implemented because `envelope_view`'s data model was never built; see A-02. Option F does not need
the narrowing -- under settle-and-bump the target canonical IS the canonical (bumped in place), not
a separate carried sibling, so a single override flag is sufficient.

A-03 verification (auditor, 2026-05-15): ACCURATE. Skip predicates verified at
`app/services/recurrence_engine.py:128` (`if existing_txn.is_override:`) and
`app/services/transfer_recurrence.py:97` (`if xfer.is_override:`). The `is_override` column on
`SoftDeleteOverridableMixin` (`app/models/mixins.py:65-103`) is a single uniform boolean; the schema
has no provenance metadata, so a "carried-only" sub-rule could not be represented even if a future
code change wanted to differentiate. Option F's "bumps in place" verified at
`app/services/carry_forward_service.py:887-896` (no sibling row is created; the existing target
canonical is mutated). See `answer_verification.md` Section 1 (A-03).

Q-04 (maps to C-03): ARM `current_principal` source for projection. `section5` (5.1-2) says current
principal must be derived from confirmed payments via engine replay and replace the stored
`LoanParams.current_principal` for projection purposes; `arm_anchor` (3F) says for ARM loans
`_compute_real_principal()` must return `current_principal` directly without replaying payments
because forward-from-origination is mathematically wrong without complete rate history. For ARM
loans specifically, should the audit verify that the code uses the stored `current_principal` (per
`arm_anchor`) or the engine-replayed value (per `section5`), and is the answer different for
fixed-rate loans?

A-04 (developer, 2026-05-13): The two plans apply to different loan types by design; they are not
contradictory. For ARM loans the code uses the stored `current_principal` directly (per `arm_anchor`
3F): `app/services/amortization_engine.py:977-985` sets
`LoanProjection.current_balance = current_principal`;
`app/services/savings_dashboard_service.py:373` reads it through `proj.current_balance`;
`app/services/year_end_summary_service.py:1465-1469` anchors the schedule at `current_principal` for
ARM. For fixed-rate loans the code walks the schedule forward from origination using confirmed
`PaymentRecord`s (per `section5`'s replay approach), same lines 977-985. The `LoanProjection`
docstring at `app/services/amortization_engine.py:848-861` documents this dual policy. Phase 3
should verify the split is consistent across every page that displays principal, not whether one
plan replaces the other.

A-04 verification (auditor, 2026-05-15): ACCURATE. All four cited locations verified.
`app/services/amortization_engine.py:977-984` branches on `is_arm` (line 977); ARM sets
`cur_balance = current_principal` (line 978); fixed-rate walks
`for row in reversed(schedule): if row.is_confirmed: cur_balance = row.remaining_balance; break`
(lines 980-984) with fallback to `current_principal`. Minor: the `LoanProjection` docstring at line
855 says fixed-rate `current_balance` is "derived from the schedule by walking to today's date", but
the implementation actually walks for the last `is_confirmed` row. The inline comment at lines
971-976 acknowledges this is deliberate ("walking to today's date would pick up theoretical
contractual rows that may not match reality"). Documentation drift, not a logic bug. See
`answer_verification.md` Section 1 (A-04).

Q-05 (maps to C-04): ARM monthly payment computation. `section5` describes the engine replaying
actual payments from origination and re-amortizing at every rate change. `arm_anchor` (1A, 1D)
introduces an anchor reset at today using `current_principal` and `current_rate`, and recomputes the
monthly payment from the anchor forward; pre-anchor rows are approximate. Inside an ARM's fixed-rate
window, these two methods can produce different monthly payment values when rate history is
incomplete. Which method is current, and is the developer's reported "fluctuating monthly payment"
symptom a manifestation of the methods being mixed across entry points?

A-05 (developer, 2026-05-13): `arm_anchor`'s anchor-reset method is current -- every code site that
computes ARM monthly payment uses the formula
`calculate_monthly_payment(current_principal, current_rate, remaining_months)` per W-048. Eight call
sites compute it: `app/services/amortization_engine.py:440`, `:491`, `:512`, `:697`, `:952`;
`app/services/balance_calculator.py:225`; `app/services/loan_payment_service.py:251`;
`app/routes/loan.py:1225`. The fluctuation symptom is not a "mixed methods" issue but an
"inconsistent inputs" issue: each call site computes `current_principal`, `current_rate`, and
`remaining_months` from its own context (`LoanParams.interest_rate` vs `rate_history` resolution;
different `remaining_months` derivations). `section5`'s engine-replay description is also still in
the code (the engine replays confirmed payments from origination forward); inside an ARM's
fixed-rate window, replay and anchor-reset must produce the same number, and any divergence is a
finding. Phase 3 must verify all eight call sites receive the same triple for the same loan on the
same date; Phase 6 should record the DRY violation across the eight sites.

A-05 verification (auditor, 2026-05-15): PARTIAL. All 8 enumerated sites exist and use the ARM
formula. But "Eight call sites compute it" understates the total: there are 16 call sites in `app/`,
plus 1 definition at `app/services/amortization_engine.py:178`. The 8 fixed-rate ELSE branches use a
different formula `(original_principal, rate, term_months)` and produce a different value for
partially-paid fixed-rate loans. Full inventory:

- ARM-formula branches (the 8 in A-05): `amortization_engine.py:440, :491, :512, :697, :952`;
  `balance_calculator.py:225`; `loan_payment_service.py:251`; `routes/loan.py:1225`.
- Fixed-rate ELSE branches (8 sites, missing from A-05):
  `amortization_engine.py:436, :693,
  :957`;`balance_calculator.py:231`;`loan_payment_service.py:256`;`routes/loan.py:1231`. These
  use`(original_principal, rate, term_months)`. Plus 2 in-loop state transitions
  inside`generate_schedule`(`:491`,`:512`, listed above with the ARM branches because they fire only
  on ARM schedules in practice) and 2 unconditional sites:`routes/loan.py:1102` (refinance preview,
  new-loan terms by design) and `app/routes/debt_strategy.py:127` (uses ARM formula on EVERY loan,
  ARM or fixed; intent unclear).

`app/routes/debt_strategy.py:127` is the 16th site, missed by both A-05 and Q-09. Q-09's "14" is
also short (its prose enumerates 7 fallback sites despite saying "six"). Three concerns Phase 3 must
verify:

1. Pairs 1-2 in `amortization_engine.py` (`:436/440`, `:693/697`) use caller-state discriminator
   (`using_contractual` = `original_principal` provided AND `term_months` provided AND no
   `rate_changes`), not the `is_arm` column. A caller that forgets `original_principal` on a
   fixed-rate loan silently routes through the ARM branch.
2. The `:952` predicate is `if is_arm and remaining > 0`. A fully-paid ARM (`remaining <= 0`)
   routes through the fixed-rate ELSE at `:957` using `orig_principal/term_months`, which
   produces a meaningless contractual figure for a paid-off loan.
3. `debt_strategy.py:127` needs developer clarification on intent.

Phase 3 must verify all 16 call sites against the per-loan invariant, not just the 8 ARM branches;
verifying only the ARM branches answers the W-048 invariant tautologically. See
`answer_verification.md` Section 1 (A-05) for the full call-site table.

A-05 addendum (developer, 2026-05-18): Resolved by E-18 (`00_priors.md` §0.3). The
anchor-reset-versus-engine-replay split is not an implementation latitude; both are unified into a
single pure resolver that replays confirmed payments forward from the most recent anchor
(origination for fixed-rate, the user-verified statement balance for ARM) and honors the ARM
fixed-rate window so the in-window payment is constant by construction. The 16-call-site DRY
violation is remediated by routing every site through that one resolver. The open tail of this
question -- `debt_strategy.py:127` calling the ARM formula unconditionally on every loan -- is
answered by the same policy: that site stops calling a formula directly and reads the resolver like
every other consumer, so the "is the unconditional ARM formula intended" question dissolves rather
than needing a separate intent decision. Phase 3 still records the divergence findings; the
remediation target is the single resolver, not per-site patching.

Q-06 (maps to C-05): Year-end mortgage interest source. `section8` (13.D) defines mortgage interest
total as the sum of interest portions from amortization schedule rows whose `payment_date` falls in
the calendar year. `year_end_fixes` (1) requires escrow subtraction from shadow transaction amounts
and biweekly-month redistribution before amortization. Should the `section8` definition stand on its
own, or is it superseded by `year_end_fixes`'s preprocessing requirements such that `section8`'s
rule is incomplete on its own?

A-06 (developer, 2026-05-13): The plans describe different layers of the same pipeline and both
apply. `year_end_fixes` is preprocessing: `app/services/loan_payment_service.py:263-353`
(`prepare_payments_for_engine`) subtracts escrow excess at lines 305-318 and redistributes biweekly
month collisions at lines 321-351 before payments reach the amortization engine. `section8` (13.D)
is aggregation: `app/services/year_end_summary_service.py:380-408` (`_compute_mortgage_interest`)
sums `row.interest` for schedule rows where `payment_date.year == year`. The exact-value test at
`tests/test_services/test_year_end_summary_service.py:1399` (`Decimal("15356.80")`) verifies the
full pipeline. `section8`'s rule is correct only when applied to a schedule generated from
`year_end_fixes`-preprocessed payments; both plans are honored simultaneously.

A-06 verification (auditor, 2026-05-15): ACCURATE. All cited locations and the end-to-end pipeline
verified: `Transaction (shadow income)` -> `get_payment_history` (`loan_payment_service.py:156-230`)
-> `prepare_payments_for_engine` (`loan_payment_service.py:263-353`, called from `load_loan_context`
at `:122-125`) -> `LoanContext.payments` -> `amortization_engine.generate_schedule` (from
`year_end_summary_service._generate_debt_schedules` at `:1471-1483`) -> `_compute_mortgage_interest`
at `year_end_summary_service.py:380-408`. Minor: the escrow subtraction loop spans
`loan_payment_service.py:305-318` with the reassignment `sorted_payments = adjusted` at line 319.
Two observations: the test at `tests/test_services/test_year_end_summary_service.py:1399` invokes
`compute_year_end_summary` end-to-end but creates zero payments, so `prepare_payments_for_engine`
short-circuits at the empty-list guard (`:297-298`); the actual reshaping is covered by dedicated
unit tests in `tests/test_services/test_loan_payment_service.py:488+`. Two paths call
`generate_schedule` WITHOUT preprocessing: `savings_dashboard_service.py:471, 488` (paid-off check)
and `routes/debt_strategy.py:175, 181` (debt-strategy current-principal). They do not affect
mortgage interest but could produce wrong schedules in their own contexts when escrow-inclusive
payments are present. See `answer_verification.md` Section 1 (A-06).

Q-07 (maps to C-06): Envelope source row `pay_period_id` after carry-forward. `carry_fwd_impl`
(Phase 4 step 7) says the envelope source row stays in its original period as a settled record;
`prod_readiness_v1`'s description of `carry_forward_unpaid` (WU-10) implies template-linked
transactions move to the target period and are flagged as overrides. These describe different
generations of carry-forward behavior. Which behavior does the current code embody for envelope vs
non-envelope rows, and is `prod_readiness_v1`'s description superseded by `carry_fwd_impl`?

A-07 (developer, 2026-05-13): The plans describe different branches of the same partition; both
behaviors coexist for different transaction shapes. `app/services/carry_forward_service.py`
partitions sources into three branches (lines 273-277): transfer (`transfer_id IS NOT NULL`),
envelope (`template.is_envelope`), and discrete (everything else). For envelope rows
(`carry_fwd_impl` W-118), source `pay_period_id` is unchanged -- `settle_from_entries` only mutates
status/paid_at/actual_amount; target canonical is bumped at lines 891-894. For discrete
template-linked rows (`prod_readiness_v1` W-192), source is moved with `pay_period_id = target` and
`is_override = True` at lines 415-416. `prod_readiness_v1`'s WU-10 description is the
discrete-branch behavior; `carry_fwd_impl`'s Phase 4 step 7 is the envelope-branch behavior. Neither
plan supersedes the other.

A-07 verification (auditor, 2026-05-15): ACCURATE. The three-way partition at
`carry_forward_service.py:272-278` verified with the exact predicates the developer described; lines
415-416 (discrete) and 891-894 (envelope target bump) verified. One nuance the answer did not
surface: the discrete branch further sub-splits at runtime.

- Template-linked discrete (`template_id IS NOT NULL`, `carry_forward_service.py:405-421`): bulk
  UPDATE sets `pay_period_id`, `is_override = True`, `version_id += 1`.
- Ad-hoc discrete (`template_id IS NULL`, `carry_forward_service.py:423-438`): bulk UPDATE sets
  only `pay_period_id` and `version_id += 1`. It does NOT set `is_override`.

Reason for the ad-hoc carve-out: ad-hoc rows are not constrained by the partial unique index
`idx_transactions_template_period_scenario` (comment at lines 382-384), so they have no canonical to
override. The sub-split is intentional. Worth adding to A-07 for completeness. See
`answer_verification.md` Section 1 (A-07).

## Behavioral ambiguities raised by Phase 1

Phase 1 sessions append a `Q-NN` here whenever a function admits two plausible behaviors and the
codebase does not pick between them by reference to a documented intent. The developer answers in
the next session.

Q-08 (P1-b, 2026-05-15): For an entry-tracked transaction whose parent status is `done` (paid),
should the dashboard's "remaining" and "over-budget" display compute against `estimated_amount` or
against `actual_amount`? Today `dashboard_service._entry_progress_fields`
(`app/services/dashboard_service.py:203-249`) reads `txn.estimated_amount` directly at line 239 (via
`entry_service.compute_remaining`) and line 245 (for the over-budget comparison), even when the
transaction is settled and `actual_amount` is non-null. Two interpretations are plausible:

- "Budget is what you allocated": the remaining-and-over-budget figures should always anchor on
  `estimated_amount` so the user sees how their original plan held up, regardless of how the
  transaction settled. The current code matches this reading.
- "Budget is what you spent": once the transaction settles, the remaining/over-budget figures
  should re-anchor on `actual_amount` so the dashboard reflects the actual outcome and any drift
  between estimate and actual is visible.

Which reading is intended? Phase 3 will flag the current code as either AGREE (interpretation 1) or
DIVERGE (interpretation 2) based on the answer.

A-08 proposed (auditor, 2026-05-15, pending developer confirmation): The current code implements
interpretation (1) "Budget = what you allocated". `_entry_progress_fields`
(`app/services/dashboard_service.py:203-246`) anchors `entry_remaining` (line 239) and
`entry_over_budget` (line 245) on `txn.estimated_amount` unconditionally. `compute_remaining` at
`app/services/entry_service.py:405-425` is `estimated_amount - sum(e.amount for e in entries)`; the
function does not receive the transaction and cannot switch on status. For a done txn with
`actual_amount = $100`, `estimated_amount = $120`, entries summing to
$80: returns `entry_remaining = $40`, `entry_over_budget =
False`; the `$100` `actual_amount` is not consulted. Separately, `bill["amount"] =
txn.effective_amount` (`dashboard_service.py:191`) returns `actual_amount` when non-null, so the
displayed bill row has internally inconsistent anchors against a single user mental model.

No test exercises (done, actual_amount set, entries present):
`tests/test_routes/test_dashboard_entries.py:45-88` hard-codes `status_id = projected.id`. The audit
cannot determine intent from the code alone. If interpretation (1) is the intent, Phase 3 records
AGREE; if interpretation (2), the code is a DIVERGE finding and `compute_remaining` needs to accept
the transaction and switch base on `is_settled`. The cross-anchor inconsistency (amount uses
`actual_amount`, remaining uses `estimated_amount`) is a separate concern worth labeling regardless
of which interpretation is chosen. See `answer_verification.md` Section 2 (Q-08).

A-08 (developer, 2026-05-18): Interpretation 1 (budget = what you allocated). Resolved by E-21
(`00_priors.md` §0.3). `remaining` and `over-budget` always anchor on `estimated_amount`,
projected or settled; the current code's remaining/over-budget base is correct and stays. The
auditor's proposed A-08 (interpretation 1 is current) is confirmed as the INTENDED behavior, so
Phase 3 records AGREE on the remaining/over-budget base. The separately-flagged cross-anchor
inconsistency (proven: `amount` = `effective_amount` returns `actual_amount` once settled at
`dashboard_service.py:191` / `transaction.py:245`, while remaining/over-budget anchor on
`estimated_amount` at `:240,245`) IS a finding regardless: the bill row must present one coherent
plan-versus-actual model with the two figures explicitly labeled, not silently mixed-base.
`compute_remaining` stays a pure `estimated_amount - sum(entries)` (no `txn`/`is_settled`
parameter). Remediation is display reconciliation/labeling only.

Q-09 (P1-b, 2026-05-15): For an ARM mortgage's `monthly_payment`, A-05 lists eight call sites that
must receive the same `(current_principal, current_rate, remaining_months)` triple for a given
loan-on-date. The grep in P1-b finds fourteen `calculate_monthly_payment` call sites, not eight: an
extra three in `amortization_engine.py` (lines 436, 693, 957), an extra one in
`balance_calculator.py` (line 231), an extra one in `loan_payment_service.py` (line 256), and two in
`app/routes/loan.py` (lines 1102, 1231). The eight in A-05 are the primary branch of each if/else
pair; the additional six are the fallback branches. Should Phase 3's consistency audit verify all
fourteen call sites against the invariant, or only the eight in A-05? If only the primary branches
matter, what guarantees do the fallback branches make about their inputs?

A-09 proposed (auditor, 2026-05-15, pending developer confirmation): The actual count is 16 call
sites in `app/`, not 14. A-05 listed 8 (the ARM branches); Q-09 listed 7 additional (despite saying
"six") and missed `app/routes/debt_strategy.py:127`. Full inventory in `answer_verification.md`
Section 1 (A-05) and Section 2 (Q-09).

Phase 3 should verify all 16 call sites, not just the 8 ARM branches. Reasons:

- The 8 fixed-rate ELSE branches (`amortization_engine.py:436, :693, :957`;
  `balance_calculator.py:231`; `loan_payment_service.py:256`; `routes/loan.py:1231`) use the
  formula `(original_principal, rate, term_months)`, NOT the ARM formula. For partially-paid
  fixed-rate loans these produce a different value than the ARM branches.
- Verifying only the 8 ARM branches answers the W-048 invariant tautologically (every ARM
  branch uses the ARM formula, by construction).
- The fluctuation symptom may originate from a fixed-rate branch firing when the ARM branch was
  expected, or from the caller-state discriminator (`using_contractual`) misclassifying a
  fixed-rate loan when `original_principal` is not passed.

Fallback branch input guarantees:

- Pairs 3-6 (`is_arm`-discriminated, sites at `:952/957`, `balance_calculator.py:225/231`,
  `loan_payment_service.py:251/256`, `routes/loan.py:1225/1231`): the ELSE branches read
  `LoanParams.original_principal` and `params.term_months`, required model columns wrapped in
  `Decimal(str(...))`. Risk axis is the discriminator (`is_arm` column correctness), not the
  inputs.
- Pairs 1-2 (`amortization_engine.py:436/440`, `:693/697`): discriminator is caller-supplied
  state (`using_contractual` = `original_principal` provided AND `term_months` provided AND no
  `rate_changes`). A caller that omits `original_principal` for a fixed-rate loan silently
  routes through the ARM branch. Phase 3 should audit every direct caller of `generate_schedule`
  and `calculate_summary` for this pattern.
- Corner case at `:952`: `if is_arm and remaining > 0` routes a fully-paid ARM (`remaining <=
  0`) through the fixed-rate ELSE at `:957`, using `orig_principal/term_months`. Likely a
  meaningless figure for a paid-off ARM.
- `routes/debt_strategy.py:127`: unconditional ARM-formula call on every loan, fixed-rate or
  ARM. Intent unclear; needs developer confirmation. For a partially-paid fixed-rate loan, this
  produces a value lower than the contractual payment.

See `answer_verification.md` Section 2 (Q-09) for the full per-site analysis.

A-09 (developer, 2026-05-18): Resolved by E-18 (`00_priors.md` §0.3). All 16 call sites (the
auditor's corrected count, not 14) must route through the single loan-state resolver; per-site
input guarantees become moot once no site computes the payment independently. `debt_strategy.py:127`
is covered by the same policy (see A-05 addendum). Phase 3 verifies the divergence as a finding
against E-18; the fix is the resolver, not reconciling 16 independent call sites.

Q-10 (P1-c, 2026-05-15): `grid.index` (`app/routes/grid.py:164`) computes per-period subtotals
(income, expense, net) inline at lines 263-279 by iterating transactions and accumulating
`txn.effective_amount` directly into Decimal aggregates. The same domain concept (`period_subtotal`)
is also produced by `dashboard_service._get_spending_comparison` and is implicit in
`balance_calculator._sum_remaining` / `_sum_all`. Two interpretations are plausible:

- "Subtotal is a display detail of the grid": route-layer aggregation is fine because the only
  consumer is the grid template; service-layer functions handle the balance computation that
  needs to be cross-page consistent. The current code matches this reading.
- "Subtotal is a shared financial concept": route-layer aggregation duplicates logic the service
  layer already owns (the balance calculator iterates the same transactions with the same status
  filter to produce running balances). The subtotal computation should move to a service so
  every consumer reads it from one source.

Which reading is intended? Phase 6 SRP review needs the answer; Phase 3 must compare `grid.index`
subtotals against the dashboard's spending-comparison values for the same period regardless, because
both are user-facing financial figures.

A-10 proposed (auditor, 2026-05-15, pending developer confirmation): The current code computes
per-period subtotal inline in the route at `app/routes/grid.py:263-279` (Projected-only, `is_income`

- `is_expense` split, `txn.effective_amount`). Three other services compute superficially-similar
"period totals" with divergent semantics:
- `dashboard._sum_settled_expenses` (`app/services/dashboard_service.py:607-633`): settled-only
  (status in DONE/RECEIVED/SETTLED), expense-only, `abs(effective_amount)`. OPPOSITE status
  filter from grid; cannot agree by construction.
- `balance_calculator._sum_remaining` and `_sum_all`
  (`app/services/balance_calculator.py:389-451`): same filters as grid (Projected-only,
  is_income + is_expense), BUT expense uses `_entry_aware_amount(txn)`
  (`balance_calculator.py:292-386`), which subtracts cleared entry debits when entries are
  loaded. For a projected envelope expense with cleared entries, the two paths produce different
  numbers.
- `spending_trend_service.period_totals` (`app/services/spending_trend_service.py:315-322`):
  expense-only, abs of effective_amount.

No service-level `period_subtotal` function exists. Grep for
`period_subtotal | period_total | subtotal` finds only `spending_trend_service.py:315` and the
template/route consumers of the `subtotals` dict the grid hands to its template.

Phase 3 must record that the grid's inline subtotal and the balance calculator's expense disagree on
`(period, Projected, envelope-with-cleared-entries)` inputs, regardless of which interpretation the
developer chooses. This is a DIVERGE the user would see if they compared the grid's subtotal row to
a running balance derived from the balance calculator (both have `selectinload(Transaction.entries)`
in effect; `grid.py:229`).

Choosing interpretation (1) "display detail of grid" matches current locality; the divergence above
still requires a Phase 3 finding. Choosing interpretation (2) "shared concept" requires deciding
between `effective_amount` and `_entry_aware_amount` as the canonical expense formula. See
`answer_verification.md` Section 2 (Q-10).

A-10 (developer, 2026-05-18): Interpretation 2 (shared financial concept). Resolved by E-25
(`00_priors.md` §0.3). The per-period subtotal moves to ONE service producer that every consumer
reads (no route-layer inline aggregation), and the canonical expense base is `_entry_aware_amount`
-- the SAME base the balance calculator uses -- so `balance[p] - balance[p-1]` equals
`subtotal.net` by construction, including the projected-envelope-with-cleared-entries case that
currently diverges. The proven divergence is a DIVERGE finding regardless; remediation is the
single producer sharing `_entry_aware_amount` with the balance pass (ideally computed in the same
pass so they cannot drift). Consistent with the E-18 through E-24 single-source-of-truth family.

Q-11 (P1-c, 2026-05-15): `loan.refinance_calculate` (`app/routes/loan.py:1027`) derives
`current_real_principal = proj.current_balance` at line 1087 from
`amortization_engine.get_loan_projection`. A-04's dual policy means `proj.current_balance` is the
stored `LoanParams.current_principal` for ARM loans and the engine-walked balance for fixed-rate
loans. The refinance dialog then optionally overrides with
`refi_principal = current_real_principal + closing_costs` at line 1095 (when the user does not
supply an explicit `new_principal` value). Is the refinance flow expected to honor the A-04 dual
policy unchanged (ARM uses stored, fixed uses walked), or should the refinance "current principal"
always come from a single canonical source regardless of loan type? Phase 3 must verify the value
the refinance form prefills matches the value rendered on `/accounts/<id>/loan` for the same
loan-on-date.

A-11 proposed (auditor, 2026-05-15, pending developer confirmation): The `/accounts/<id>/loan`
dashboard does NOT honor the A-04 dual policy on the display side. This is a real divergence.

The dashboard route at `app/routes/loan.py:405-575` constructs `proj = get_loan_projection(...)`
(line 429) but the template at `app/templates/loan/dashboard.html:104` renders
`${{ "{:,.2f}".format(params.current_principal|float) }}` directly, bypassing the projection
entirely. The refinance route at `app/routes/loan.py:1087` uses `proj.current_balance`.

For ARM loans the two values coincide because `app/services/amortization_engine.py:978` assigns
`cur_balance = current_principal`. For fixed-rate loans with any confirmed payments,
`proj.current_balance` is the last `is_confirmed` row's `remaining_balance`
(`amortization_engine.py:980-984`), which may differ from the stored `params.current_principal`.
Only when no confirmed payments exist (the fallback at line 980) do the two values match.

Phase 3 finding: for a fixed-rate loan with any confirmed payments, the refinance form prefill (when
"New Principal" is blank, the auto-calc at `loan.py:1095`) does NOT match the "Current Principal"
card on `/accounts/<id>/loan`. The refinance prefill is the more accurate number (reflects committed
payments); the dashboard's display value is the stored static.

Developer must decide: either render `proj.current_balance` on the dashboard (pass it via the
existing engine call at line 429) so prefill matches the on-screen number, or rename the dashboard
label to make the divergence intentional and visible ("Stored Principal" vs "Current Balance" or
similar). See `answer_verification.md` Section 2 (Q-11).

A-11 (developer, 2026-05-18): Resolved by E-18 (`00_priors.md` §0.3). Neither "render
`proj.current_balance`" nor "relabel the card" is the answer; both still treat a stored mirror as a
value that exists. Under E-18 there is no authoritative stored `current_principal` and therefore no
second value to reconcile: the dashboard card, the refinance prefill, debt strategy, and net worth
all read the same single resolver, so they are equal by construction for the same loan-on-date.
F-016 and the loan side of F-001 remain DIVERGE findings; the remediation target is the single
resolver.

Q-12 (P1-c, 2026-05-15): `obligations.summary` (`app/routes/obligations.py:259`) builds monthly
equivalents for recurring templates inline at lines 331-395 by calling
`savings_goal_service.amount_to_monthly` per template in a loop and then aggregating the totals with
Decimal arithmetic at lines 398-408. The result feeds the `/obligations` page's `net_cash_flow` row.
The same monthly-equivalent normalization is needed elsewhere -- the dashboard's cash-runway
computation (`dashboard_service._compute_cash_runway` uses paid expenses over 30 days, NOT
recurring-template monthly equivalents, but the conceptual overlap is real) and the
savings-dashboard's DTI denominator (`savings_dashboard_service._compute_debt_summary`). Should the
per-template loop and aggregation move into a dedicated service so every consumer reads from one
canonical monthly- equivalent aggregator? If not, what is the contract that distinguishes the three
call paths so Phase 3 can verify each produces a consistent number when their inputs overlap?

A-12 proposed (auditor, 2026-05-15, pending developer confirmation): The three paths answer
different questions; the code makes no guarantee they agree.

- `obligations.summary` (`app/routes/obligations.py:259-423`): forward-projected monthly from
  active recurring `TransactionTemplate` + `TransferTemplate`. Excludes `ONCE` patterns (lines
  333-334, 356-357, 378-379). Excludes templates with `end_date < today` (lines 335, 358, 380).
  Calls `amount_to_monthly(amount, pattern_id, interval_n)` at lines 338, 361, 383.
- `dashboard._compute_cash_runway` (`app/services/dashboard_service.py:375-417`): trailing
  30-day average from SETTLED `Transaction` rows (status in DONE/RECEIVED/SETTLED, expense-only).
  Does NOT consult templates. Returns days of runway, not monthly equivalent.
- `savings_dashboard._compute_debt_summary`
  (`app/services/savings_dashboard_service.py:802-876`): amortization-engine P+I
  (`ad["monthly_payment"]` from `get_loan_projection` at `:362-367`) plus escrow `annual / 12`
  (`escrow_calculator.calculate_monthly_escrow`). Does NOT call `amount_to_monthly` and does
  NOT consult templates.

Cash-runway and debt-summary are conceptually independent from obligations: realised history,
amortization-derived obligation, and forward template projection are different quantities. A single
aggregator only makes sense within the obligations path itself (three near-identical inline loops at
obligations.py:331-395 could be extracted to a helper).

The 26/12 biweekly-to-monthly conversion factor is duplicated in three places:
`savings_goal_service.py:17-18` (`_PAY_PERIODS_PER_YEAR` / `_MONTHS_PER_YEAR`),
`savings_dashboard_service.py:170-172` (inline `Decimal("26") / Decimal("12")`), and
`savings_dashboard_service.py:765` (same inline form). Numerically equivalent; not cross-imported.

Two real defects surfaced by this question (out of Q-12 scope but reported per CLAUDE.md rule 4):

1. `compute_committed_monthly` at `app/services/savings_goal_service.py:287-328` does NOT skip
   templates with `end_date < today`, while `obligations.summary` does. Consumers (emergency-fund
   baseline at `savings_dashboard_service.py:794`, per-goal contributions at `:700`) include
   expired-template contributions indefinitely.
2. Mortgage / loan double-counting risk: a user with both (a) a recurring expense template for
   the mortgage AND (b) a loan account with `loan_params` sees the same payment in
   `/obligations.total_expense_monthly` AND in the savings dashboard DTI numerator. No
   reconciliation guard.

Phase 3 contract for verification: for the same set of active recurring expense templates with
`pattern_id != ONCE` AND `(end_date IS NULL OR end_date >= today)`,
`obligations.summary.total_expense_monthly` MUST equal the sum of `amount_to_monthly` outputs.
`cash_runway` and `_compute_debt_summary` have no such relationship with obligations. See
`answer_verification.md` Section 2 (Q-12).

A-12 (developer, 2026-05-18): Resolved by E-24 (`00_priors.md` §0.3).

- DRY aggregator: the three structurally-identical loops (`obligations.py:331-395`) and the
  parallel `compute_committed_monthly` logic collapse into ONE canonical monthly-equivalent
  aggregator with a single shared filter rule (skip ONCE, skip `end_date < today`). The 26/12
  factor is defined once and imported, not re-inlined.
- Defect #1 (PROVEN): confirmed -- `compute_committed_monthly` (`savings_goal_service.py:287-325`)
  omits the `end_date < today` skip that `obligations.summary` applies, so expired-template
  contributions inflate the EF baseline and per-goal floors indefinitely. Fixed by the single
  shared filter in the canonical aggregator (all consumers at once).
- Defect #2 (PROVEN): confirmed -- one loan obligation is represented two unreconciled ways
  (committed transfer template vs amortization PITI), divergent across `/obligations`, the EF
  baseline, and the DTI card, with a latent double-count trap. Resolution: an explicit nullable
  FK linking a recurring template to the loan account it funds. Developer rationale (recorded
  verbatim intent): a committed template amount is REAL even when it differs from the amortization
  obligation -- so neither blind exclusion nor independence is correct. With the link, debt/DTI
  uses the amortization figure, cash-flow/obligations/EF uses the committed template figure (each
  counted once), and the template-minus-required delta is surfaced to the user as voluntary extra
  principal (not a silent sub-dollar phantom bug, not a silent $100 masquerading as required).
- Three top-level paths (`obligations.summary` / `_compute_cash_runway` / `_compute_debt_summary`)
  remain intentionally distinct questions, NOT required to agree; only the obligations-internal
  aggregation is unified. The auditor's A-12 reading is confirmed on that point.

Phase 3 records defects #1 and #2 as DIVERGE against E-24; the FK migration follows the
explicit-`ondelete` (`SET NULL`) and three-step nullable-add rules.

Q-13 (P1-c, 2026-05-15): `salary.calibrate_preview` (`app/routes/salary.py:1064`) computes the
calibration's taxable-income input inline at line 1095 (`taxable = gross - total_pre_tax`), even
though `paycheck_calculator.calculate_paycheck` produces a `breakdown.taxable_income` field on its
return value. The route uses `bk.total_pre_tax` (line 1091) to compute its own subtraction but does
NOT read `bk.taxable_income`. Should the route read `bk.taxable_income` directly so the
calibration's effective rates are derived against the same taxable-income value the breakdown
reports, or is the route's inline subtraction the intended source (and the breakdown's field then
potentially divergent)? Phase 3 must verify the two values agree for identical inputs.

A-13 proposed (auditor, 2026-05-15, pending developer confirmation): The route's inline
`taxable = gross - bk.total_pre_tax` at `app/routes/salary.py:1095` and `bk.taxable_income` at
`app/services/paycheck_calculator.py:155-157` measure different quantities by design. They agree
only when `actual_gross_pay == bk.gross_biweekly`, the trivial case calibration is designed to
detect deviation from.

- Route: `taxable = data["actual_gross_pay"] - bk.total_pre_tax` (pay-stub-grounded).
- Breakdown: `bk.taxable_income = max(bk.gross_biweekly - total_pre_tax, 0)` where
  `bk.gross_biweekly = (annual_salary / pay_periods_per_year).quantize(...)`
  (`paycheck_calculator.py:133-135`) (profile-grounded, floored at zero at lines 156-157).

The route's intent is pay-stub-grounded taxable so calibration's effective rates derive from the
user's actual paycheck. Using `bk.taxable_income` directly would defeat the calibration use case.

However, a real bug surfaced: `bk.total_pre_tax` includes percentage-based pre-tax deductions
computed against the PROFILE's `gross_biweekly`, not against the form's `actual_gross_pay`. At
`paycheck_calculator.py:439-442`:

    if ded.calc_method_id == calc_method_pct_id:
        amount = (gross_biweekly * amount).quantize(
            TWO_PLACES, rounding=ROUND_HALF_UP
        )

When the form's gross differs from the profile's gross (the calibration use case), the percentage
deduction is applied to the wrong base, and the derived effective rates absorb the gap. Concrete
example: profile `annual_salary = $60,000` so `bk.gross_biweekly = $2,307.69`; pay stub
`actual_gross_pay = $2,400.00`; pre-tax 401k is 5% of gross. Then:

- `bk.total_pre_tax = 2,307.69 * 0.05 = $115.38`
- Route's `taxable = 2,400.00 - 115.38 = $2,284.62`
- If the 5% were applied to the actual stub gross: `2,400.00 * 0.05 = $120.00`, so taxable
  would be `$2,280.00`.

Fix needs developer intent: option A (recompute pre-tax deductions inline against `actual_gross_pay`
via a refactored helper), option B (use `bk.taxable_income / bk.gross_biweekly` as a ratio to
scale), option C (status quo, document the bias). Option A is what calibration semantically wants.
See `answer_verification.md` Section 2 (Q-13).

A-13 (developer, 2026-05-18): PROVEN DEFECT confirmed; resolved by E-20 (`00_priors.md` §0.3) as
Option A' (a refinement of the auditor's Option A). The fix is not to recompute the profile's
configured pre-tax rules against `actual_gross_pay`, but to collect the actual pre-tax deduction
total as a new pay-stub field so the taxable base is 100% pay-stub-grounded:
`taxable = actual_gross_pay - actual_pre_tax_deductions`. Option B (ratio scale) and Option C
(document the bias) are rejected (B is exact only when all pre-tax deductions are percentage-based;
C is unacceptable for a money calculation). This is the concrete form of the A-25 sub-1
immutable-snapshot remediation: effective rates derived server-side at confirm from posted
`actual_*` values, never accepted as hidden client inputs. Scope addition acknowledged and
accepted (new `actual_pre_tax_deductions` field + `CalibrationSchema`/`CalibrationConfirmSchema` +
template + snapshot semantics); correctness over scope minimisation per the project mandate.
Phase 3 records the taxable-base defect and the A-25 hidden-input gap as DIVERGE against E-20.

Q-14 (P1-c, 2026-05-15): `dashboard.mark_paid` (`app/routes/dashboard.py:54-139`) was initially
classified out-of-scope by the route-inventory subagent because its body "updates status/amount in
DB and returns a partial row." Spot-checking the response path showed it returns
`dashboard/_bill_row.html`, which re-renders `effective_amount`, `entry_remaining` (via
`_entry_progress_fields`), and the `goal_progress` percent for the just-paid bill -- all
controlled-vocabulary financial figures. The handler was therefore re-classified IN scope. The
question for the developer is whether `mark_paid` and `transactions.mark_done`
(`app/routes/transactions.py:491`) are path-equivalent: both transition a transaction from projected
to done/received, both render an updated cell partial showing `effective_amount`. Are they intended
to produce the same `effective_amount` and `entry_remaining` values for the same transaction, with
the only difference being which endpoint the UI calls? If so, Phase 3 must verify equivalence; if
not, the documented difference belongs in section 0.3 as an expected behavioral nuance.

A-14 proposed (auditor, 2026-05-15, pending developer confirmation): `mark_paid`
(`app/routes/dashboard.py:54-138`) and `mark_done` (`app/routes/transactions.py:491-629`) are
path-similar but NOT path-equivalent. They share the status policy (RECEIVED for income, DONE for
expense, DONE for transfer shadows) and the same `MarkDoneSchema` for form validation, but they
diverge on five behaviors:

1. Envelope-with-entries auto-settle. `mark_done` calls
   `transaction_service.settle_from_entries(txn)` at `transactions.py:596`, which auto-writes
   `actual_amount = sum(entries)` at `transaction_service.py:153`. `mark_paid` never calls this;
   it writes `actual_amount` only when the form provided it (`dashboard.py:127-128`). For an
   envelope-tracked txn with entries, the two endpoints produce DIFFERENT `actual_amount` for
   the same input request, and therefore different `Transaction.effective_amount` (the property
   at `app/models/transaction.py:222-245` returns `actual_amount` when non-null else
   `estimated_amount`). `mark_done` produces `sum(entries)`; `mark_paid` leaves `actual_amount`
   NULL and `effective_amount` returns `estimated_amount`. This is the most important divergence.
2. Stale-data handling. `mark_done` catches `StaleDataError` and returns a 409 conflict cell
   (`transactions.py:618`); `mark_paid` only catches `IntegrityError` (`dashboard.py:132`).
   Concurrent edits raise 409 in one path and likely 500 in the other.
3. Ownership scope. `mark_done` allows companions for templates with `companion_visible=True`
   (`_get_accessible_transaction_for_status` at `transactions.py:210-241`). `mark_paid` is
   owner-only (`_get_owned_transaction` at `dashboard.py:181-192`).
4. Rendered partial and HX-Trigger. `mark_paid` returns `dashboard/_bill_row.html` with
   `HX-Trigger: dashboardRefresh`; `mark_done` returns `grid/_transaction_cell.html` (via
   `_render_cell` at `transactions.py:628`) with `gridRefresh`. The progress display gating
   differs (bill row: `not bill.is_paid`; grid cell: `status_id == STATUS_PROJECTED`),
   semantically equivalent for a just-settled txn.
5. Logging. `mark_done` emits an info log; `mark_paid` does not.

The arithmetic for `entry_remaining` is identical in both paths (`estimated_amount - sum(entries)`);
the displayed value differs only by template gating.

Phase 3 verdict: the two endpoints produce the same `effective_amount` for transactions that are NOT
envelope-tracked or that have no entries. They produce DIFFERENT `effective_amount` for
envelope-tracked transactions with entries, the case the developer's concern is most likely to
involve.

Recommendation: either align `mark_paid` to call `settle_from_entries` for envelope-tracked
transactions (so the two endpoints agree on `actual_amount` and therefore `effective_amount`), or
document the difference explicitly so future contributors do not assume equivalence. See
`answer_verification.md` Section 2 (Q-14).

A-14 (developer, 2026-05-18): All five auditor-proposed divergences confirmed from source.
Resolved by E-23 (`00_priors.md` §0.3): NOT path-equivalence-by-coincidence and NOT
document-as-nuance, but unification -- "settle a transaction" becomes one service orchestrator
both routes delegate to (transfer-shadow -> `transfer_service`; envelope+entries ->
`settle_from_entries`; else -> one `verify_transition` + status/paid_at/optional actual; one
`StaleDataError` contract). This fixes #1 (the proven envelope `effective_amount` divergence) and
#2 (`mark_paid`'s missing `StaleDataError` -> latent 500) together; #5 (logging parity) falls
out. #3 ownership scope: developer-confirmed INTENTIONAL -- companions operate on the grid and do
not reach the dashboard (origin of the `@require_owner` blanket); the accessor stays per-route, no
finding. #4 partial/HX-Trigger: legitimate per-surface difference, documented, no finding.
Developer-stated product context (out of audit scope, recorded not adjudicated): the dashboard
mark-paid affordance does not clearly communicate which expense/pay-period is being settled and is
a redesign-or-removal candidate; if removed, `mark_paid` retires and E-23 holds with only
`mark_done`. Phase 3 records #1/#2 as DIVERGE against E-23; remediation is the shared orchestrator
plus a regression test asserting identical `effective_amount`/`entry_remaining` from both routes
for an envelope-tracked transaction with entries.

## Concept-catalog primary-path questions raised by Phase 2

Phase 2 sessions append a `Q-NN` here whenever a concept has more than one producer and the codebase
does not designate which is the canonical source of truth. The developer answers in the next
session; Phase 2's primary-path entry cross-links the answer.

Q-15 (P2-a, 2026-05-15): There is no single canonical producer for a multi-account aggregate balance
figure, and two independent per-account dispatch implementations exist. Concretely:

- Aggregate `debt_total`: `savings_dashboard_service._compute_debt_summary`
  (`app/services/savings_dashboard_service.py:802`, `total_debt` at line 855) sums the stored
  `LoanParams.current_principal` (`Decimal(str(lp.current_principal))` at line 840), while the
  liability contribution to net worth `year_end_summary_service._compute_net_worth`
  (`app/services/year_end_summary_service.py:689`) derives each loan's balance from the
  amortization schedule via `_build_account_data` / `_get_account_balance_map`
  (`app/services/year_end_summary_service.py:750`). Per A-04 the stored column and the
  engine-walked balance differ for a fixed-rate loan with confirmed payments, so the dashboard /
  `/savings` debt card and the net-worth liability figure can disagree for the same loan.
- Aggregate `account_balance` / `savings_total`: the per-account dispatch (which engine per
  account type) is implemented twice -- `_compute_account_projections`
  (`app/services/savings_dashboard_service.py:294`, drives `/savings` and the dashboard) and
  `_build_account_data` / `_get_account_balance_map`
  (`app/services/year_end_summary_service.py:750`, drives net worth and the year-end savings
  progress). Roadmap v5 Section 2 Stage A names "a single canonical balance computation" as
  PENDING (`docs/audits/financial_calculations/00_priors.md:152`); net_worth_amort W-152
  ("Net worth section and savings progress section must use identical calculation paths for all
  account types") is `planned-per-plan`, i.e. not yet implemented.

Question for the developer: which producer is the canonical source of truth that the displayed
aggregate `debt_total` (and, by extension, `account_balance` / `savings_total`) must derive from --
the savings-dashboard dispatch (`_compute_account_projections` / `_compute_debt_summary`, stored
`current_principal` base) or the year-end dispatch (`_get_account_balance_map`, amortization-real
base) -- so Phase 3 knows which side is the reference when E-04 (same number on every page) is
violated?

Why it matters: determines the primary path for `account_balance`, `savings_total`, and `debt_total`
in `02_concepts.md` (P2-a); governs the symptom #5 (`/accounts` vs other pages divergence)
hypothesis tree in Phase 5; the answer also tells Phase 3 whether the net_worth_amort W-152
plan-drift is a "code must catch up to plan" finding or a "plan superseded" finding.

Where it came up: `docs/audits/financial_calculations/02_concepts.md` (P2-a), `account_balance` /
`savings_total` / `debt_total` primary-path entries;
`app/services/savings_dashboard_service.py:294,802` and
`app/services/year_end_summary_service.py:689,750`.

A-15 (developer, 2026-05-18): Resolved by E-18 (`00_priors.md` §0.3). The question asked which of
two per-account dispatch implementations (savings-dashboard stored-`current_principal` base vs
year-end amortization-real base) is canonical. Under E-18 the answer is neither-as-written: the
stored-`current_principal` base is retired entirely, and both `debt_total` and
`account_balance` / `savings_total` derive from the single loan-state resolver (the same
event-replayed balance the year-end path approximates today). The two duplicate dispatch
implementations (`_compute_account_projections` / `_compute_debt_summary` and
`_build_account_data` / `_get_account_balance_map`) must collapse to one path that reads the
resolver -- this is the W-152 "single canonical balance computation" requirement, now a
"code must catch up to E-18" finding, not "plan superseded". Phase 3 records the E-04 / F-008
divergences against E-18; the within-`/savings` account-card-vs-debt-card mismatch (Q-22
sub-question 2) is resolved the same way (both cards read the resolver).

## Behavioral divergences raised by Phase 3

Phase 3 sessions append a `Q-NN` here whenever the consistency audit finds the code does the same
thing more than one way and no documented intent picks the canonical behavior. The auditor records
the divergence as a finding (provable from code) and STOPS on the "which behavior is intended"
question for the developer (audit-plan section 9; hard rule 5 -- no guessed verdict).

Q-16 (P3-a, 2026-05-15): When a checking/savings account has `current_anchor_period_id IS NULL`
(no anchor period set), the five balance producers do three different things for the same account:

- Grid (`app/routes/grid.py:239-241`): passes `None` as `anchor_period_id`; the engine
  (`app/services/balance_calculator.py:69-86`) matches no anchor period, so `running_balance`
  stays `None` and every period hits `continue` (`:82-84`) -- `balances` is empty and the grid
  renders a blank balance row.
- `/accounts` checking detail (`app/routes/accounts.py:1419-1421`) and `/savings`
  (`app/services/savings_dashboard_service.py:326-328`): `... or (current_period.id if ...)`
  falls back to the current period as the anchor with a `$0.00` anchor balance, producing a
  populated (but anchored-at-zero) projection.
- Net worth (`app/services/year_end_summary_service.py:2065-2066`) and the dashboard
  (`app/services/dashboard_service.py:683-684`): `return None`, omitting the account from the
  net-worth total and the dashboard balance card entirely.

Question for the developer: what is the intended behavior when an account has no anchor period --
blank (grid), a `$0`-anchored projection (`/accounts`, `/savings`), or omit the account (net
worth, dashboard)? Phase 3 records the divergence as SCOPE_DRIFT in F-001/F-003/F-006/F-007
regardless; the answer tells Phase 8 which of the three is the bug and which is the target.

Why it matters: governs the SCOPE_DRIFT axis in F-001 (`account_balance`), F-003
(`projected_end_balance`), F-006 (`net_worth`), and F-007 (`savings_total`); a user who has
created an account but not yet set its anchor sees it as blank on the grid, as `$0` on
`/accounts` and `/savings`, and as absent from net worth and the dashboard -- four different
representations of one account state, an unlabeled difference under developer expectation E-04
(`00_priors.md:178-182`).

Where it came up: `docs/audits/financial_calculations/03_consistency.md` (P3-a), F-001 / F-003 /
F-006 / F-007 anchor-handling dimension; `app/routes/grid.py:239-241`,
`app/routes/accounts.py:1419-1421`, `app/services/savings_dashboard_service.py:326-328`,
`app/services/year_end_summary_service.py:2065-2066`, `app/services/dashboard_service.py:683-684`.

A-16 (developer, 2026-05-18): Resolved by E-19 (`00_priors.md` §0.3). The question "blank,
`$0`-anchored, or omit" is answered by removing the state, not by choosing one of the three. The
`current_anchor_period_id IS NULL` state is eliminated: account creation establishes a dated t0
anchor `(current_anchor_balance, anchor_date)` plus a t0 `AccountAnchorHistory` row, and the
effective anchor pay period is resolved on read by one resolver (first pay period whose range
contains or follows `anchor_date`) that all five producers call. Verified-from-source basis: the
NULL state is the guaranteed default of every signup/seed (`auth_service.py:781-786`,
`seed_user.py:143-149`; neither generates pay periods), so it is not an edge case but the universal
new-user state, which is why a defined uniform behavior -- not a per-consumer NULL branch -- is
required. F-001/F-003/F-006/F-007 stay SCOPE_DRIFT/DIVERGE findings; the remediation target is the
single date-anchored resolver. Consistent with A-20 and A-21.

Q-17 (P3-b, 2026-05-15): For a 5/5 ARM inside its fixed-rate window (e.g. the first 60 months,
`LoanParams.arm_first_adjustment_months = 60`), the displayed "Monthly P&I" is computed by
`get_loan_projection` at `app/services/amortization_engine.py:952-954` as
`calculate_monthly_payment(current_principal, rate, remaining)` where
`current_principal = Decimal(str(params.current_principal))` (`:913`, the STORED column) and
`remaining = calculate_remaining_months(params.origination_date, params.term_months)`
(`:908-910`, a calendar formula that decreases by 1 every month). The stored
`current_principal` is not reduced as transfers settle (Q-3/A-3 / F-014: the only writer is the
manual `update_params` form at `loan.py:672-674`). Re-amortizing a non-decreasing principal
over a strictly decreasing `remaining` makes the payment drift upward a few dollars every month
even with no rate change and no manual edit -- the developer's symptom #4. The
`arm_first_adjustment_months` / `arm_adjustment_interval_months` columns
(`app/models/loan_params.py:60-61`) are stored and form-bound but consumed by zero calculation
sites (grep-verified), so the engine has no representation of the fixed-rate window. Two
plausible intended behaviors:

- "The stored column is the anchor and must be maintained": per A-04 the ARM
  `current_principal` is authoritative; the intended fix is that confirmed transfers reduce the
  stored column (closing the symptom-#3 gap), after which re-amortizing the *true* remaining
  balance over the shrinking `remaining` reproduces the constant payment via the amortization
  identity. Site 7 is then correct and the bug is purely the missing settle-driven update
  (F-014).
- "The engine must hold the payment constant in the fixed window": site 7 should re-amortize
  `proj.current_balance` (engine-real) over `remaining`, or reset the payment only at
  `arm_first_adjustment_months` / `arm_adjustment_interval_months` boundaries, so the displayed
  payment is invariant for the whole window regardless of whether the stored column is
  maintained.

Which behavior is intended? E-02 (`00_priors.md:166-170`) requires the payment be one value
for the whole fixed window; the audit records the current code as a DIVERGE (F-026) regardless,
but the answer determines whether the remediation target is F-014 (maintain the stored column
on settle) or a change at `amortization_engine.py:952-954` (re-amortize the engine-real
balance / honor the ARM-adjustment columns).

Why it matters: governs the remediation direction for F-013 (`monthly_payment` symptom #2) and
F-026 (5/5 ARM symptom #4), and ties them to F-014 (symptom #3, the un-maintained stored
`current_principal`). The verdict (DIVERGE) is not blocked; only the fix shape is.

Where it came up: `docs/audits/financial_calculations/03_consistency.md` (P3-b), F-013 and
F-026; `app/services/amortization_engine.py:908-959`, `app/models/loan_params.py:60-61`,
`app/routes/loan.py:672-674`. Cross-link Q-09, Q-11, Q-15, A-04/A-05 verification (this file),
E-02 (`00_priors.md:166-170`).

A-17 (developer, 2026-05-18): Resolved by E-18 (`00_priors.md` §0.3). The two interpretations
offered ("maintain the stored column on settle" vs "re-amortize the engine-real balance / honor the
ARM-adjustment columns") are unified, not chosen between. There is no authoritative stored
`current_principal` to maintain; the ARM payment is computed by the single resolver, which (a)
replays confirmed payments forward from the latest user-verified anchor so the principal it
amortizes is the true reduced balance, and (b) honors `arm_first_adjustment_months` /
`arm_adjustment_interval_months` so the payment is constant for the whole fixed window by
construction rather than drifting as `calculate_remaining_months` shrinks. Symptom #4 closes
because the principal decreases as payments clear (it is event-derived, never frozen). F-013 and
F-026 stay DIVERGE; the remediation target is the resolver, and this answer is consistent with
Q-22 and Q-23 by design (one stored-mirror policy, per the P4-e addendum's requirement that they
be answered together).

Q-18 (P3-cmp-3, 2026-05-16): The calendar's "month-end balance" (the "End Balance" figure on
`/analytics` calendar month view) is computed by `_compute_month_end_balance`
(`app/services/calendar_service.py:435-489`). It selects the target period by
`for p in all_periods: if p.end_date <= last_day: target_period = p`
(`app/services/calendar_service.py:461-466`, `all_periods` ordered by `period_index`,
`last_day` = the calendar month's last day) -- i.e. the LAST pay period whose `end_date` is
on or before the month's last day, then returns `balances.get(target_period.id)`. The
section8 watchlist claim W-273-cluster (`00_priors.md:524`, plan section 5.D) states the
month-end balance must be "the projected end balance of the last pay period ending in or
after the month." When a pay period straddles the month boundary (starts in-month, ends in
the following month), the code excludes it (its `end_date > last_day`) and instead reports
the projected end balance of the PRIOR period, which can be up to ~13 days before the actual
calendar month-end. Two plausible intended behaviors: (1) "balance as of the last completed
pay period within the month" -- the current code; the displayed figure is a real period-end
projection but not the literal calendar month-end. (2) "balance covering the whole month" --
the period that contains (or first ends on/after) the month-end day, so the figure reflects
every transaction through month-end. Which is intended?

Why it matters: governs the W-277 verdict in P3-cmp-3 (the period-selection axis is recorded
UNKNOWN-Q-18). The displayed "End Balance" is a user-facing money figure on the calendar; an
off-by-one-period selection silently shows a different dollar amount than the user expects
for "month-end," and it interacts with the F-003/F-009 entries-load SILENT_DRIFT (the
calendar path does NOT `selectinload(Transaction.entries)`, `calendar_service.py:471-480`)
and the Q-16 anchor-None axis (`calendar_service.py:449-450` returns `Decimal("0")` when
`current_anchor_period_id is None`).

Where it came up: `docs/audits/financial_calculations/03_consistency.md` (P3-cmp-3), W-277;
`app/services/calendar_service.py:435-489` (esp. `:461-466` period selection, `:471-480`
no-entries-load, `:449-450` anchor-None). Cross-link F-003, F-009, Q-16,
W-273 (`00_priors.md:524`).

A-18 (developer, 2026-05-18): Interpretation 2 sharpened to its exact form. Resolved by E-27
(`00_priors.md` §0.3). Neither the current "last period ending on/before month-end" (undershoot,
proven `calendar_service.py:461-466`) nor the W-273 "period covering month-end" (overshoot --
includes early-next-month transactions) is accepted; "End Balance" means the projected balance as
of EXACTLY the calendar month-end date, with the straddling period contributing only its
transactions effective on/before month-end. Computed through ONE canonical entries-aware "balance
as of date" path shared with `balance_calculator` (reconciles with grid/`/savings` per E-25);
anchor supplied by the E-19 date-anchored resolver (the `:449-450` Decimal("0") branch is
subsumed). The calendar entries-load gap (`:471-480`, no `selectinload`) is the F-003/F-009
instance and is closed by routing through the canonical path. Open implementation detail
explicitly NOT pre-decided: the per-transaction effective-date rule within a straddling period
(transactions carry `pay_period_id` + nullable `due_date` only) must be defined and documented at
remediation; the audit does not assume the data model trivially supports day-level partitioning.
W-277 period-selection axis moves from UNKNOWN-Q-18 to DIVERGE against E-27.

Q-19 (P3-cmp-3, 2026-05-16): `archive_helpers.template_has_paid_history`
(`app/utils/archive_helpers.py:17-38`) decides whether a transaction template may be
permanently hard-deleted (`app/routes/templates.py:581-637`). It blocks deletion only when a
linked, non-soft-deleted transaction has `status_id in [DONE, SETTLED]`
(`app/utils/archive_helpers.py:29-37`). RECEIVED is not in that set, yet RECEIVED is a
settled-equivalent state (`Status.is_settled = True` for Received,
`app/ref_seeds.py:79-84`) and is exactly the status `mark_done` assigns to income
transactions (`app/routes/transactions.py:534-535`: `if txn.is_income: status_id =
RECEIVED`). A transaction template whose generated income transactions were all marked
RECEIVED (and never further transitioned to SETTLED) therefore returns
`template_has_paid_history = False`, so `hard_delete_template` proceeds to
`db.session.query(Transaction).filter(Transaction.template_id == template.id).delete()`
(`app/routes/templates.py:616-618`) and permanently destroys the RECEIVED income history.
The section5a watchlist claim W-262 (`00_priors.md:509`, plan section 5A.5-2) is phrased
"templates with Paid or Settled history cannot be permanently deleted" -- the code matches
that literal wording ([DONE = Paid, SETTLED]), so the question is whether the plan's
"Paid or Settled" definition is itself too narrow for income templates: should the guard
also treat RECEIVED (and any `is_settled = True` status) as blocking history, or is
hard-deleting a RECEIVED-only income template's history intended? (The transfer analogue
`transfer_template_has_paid_history`, `app/utils/archive_helpers.py:41-62`, has no such gap:
transfers only ever use DONE/SETTLED -- the transfer service sets DONE on both shadows,
`app/routes/transactions.py:541-545` -- so [DONE, SETTLED] is complete there.)

Why it matters: governs the W-262 escalation in P3-cmp-3. This is a silent data-loss path on
an explicit user delete action (no wrong number on a page, but irreversible loss of settled
income transaction history). The fix shape depends on intent: widen the predicate to
`Status.is_settled = True` (catches RECEIVED + any future settled-equivalent), enumerate
`[DONE, RECEIVED, SETTLED]`, or document that RECEIVED-only income history is intentionally
hard-deletable. The auditor does not choose (audit-plan section 9).

Where it came up: `docs/audits/financial_calculations/03_consistency.md` (P3-cmp-3), W-262;
`app/utils/archive_helpers.py:17-38` (predicate), `app/routes/templates.py:581-637`
(hard-delete path, esp. `:616-618` the unconditional delete), `app/ref_seeds.py:79-84`
(Received `is_settled = True`), `app/routes/transactions.py:534-535` (income -> RECEIVED).
Cross-link W-262, W-264 (the transfer analogue that does NOT have the gap), F-031.

A-19 (developer, 2026-05-18): PROVEN silent irreversible data-loss path confirmed; the plan's
"Paid or Settled" wording (W-262) is itself too narrow for income templates. Resolved by E-22
(`00_priors.md` §0.3). The deletion-blocking predicate is widened to the semantic boolean --
block when any linked non-deleted transaction has a status with `is_settled = True` -- NOT an
enumerated `[DONE, RECEIVED, SETTLED]` list (enumeration is the E-15 anti-pattern that caused this
and would re-drift on a future settled-equivalent status). Applied to BOTH
`template_has_paid_history` and `transfer_template_has_paid_history` for DRY/consistency even
though only the transaction path has the current gap. Additionally, defense-in-depth: the
`hard_delete_template` delete (`templates.py:614-617`) is constrained to non-settled rows so a
future guard regression can never physically destroy settled history (two enforcement points of
one invariant; no behavior change in the correct path). Verified-from-source basis: the full
chain `archive_helpers.py:29-37` -> `transactions.py:534-535` -> `ref_seeds.py:81` ->
`templates.py:614-617` reproduced this session; sibling predicates `account_has_history` /
`category_has_usage` do not filter by status and have no analogous gap (in scope, no further
action). Phase 3 records this as a critical DIVERGE (silent data loss) with a mandatory
regression test for the RECEIVED-only income-template delete path. Not intended behavior; the
`templates.py:610-613` guard comment proves the opposite invariant.

## Source-of-truth questions raised by Phase 4

Phase 4 sessions append a `Q-NN` here whenever a stored column's role
(AUTHORITATIVE / CACHED / DERIVED) cannot be classified without developer
intent, or whenever Phase-4 re-verification finds a prior-phase document
miscited. The auditor records the drift surface from code and STOPS on the
intent question (audit-plan section 9; hard rule 5).

Q-20 (P4-a, 2026-05-16) -- **sharpens Q-16.** `budget.accounts.current_anchor_period_id`
is `NULL` for every newly-registered user (`app/services/auth_service.py:781-786`
creates the default "Checking" account with `current_anchor_balance=0` and the
period column unset) and is re-reachable post-creation any time a balance is
edited while `pay_period_service.get_current_period` returns None
(`app/routes/accounts.py:460,794` gate the period assignment on
`if current_period:`). The codebase nowhere defines what `NULL` means; five
runtime producers do four different things with the SAME stored row, an
unlabeled difference under developer expectation E-04 (`00_priors.md:178-182`):

- **blank** -- grid: `app/routes/grid.py:239-241` passes the NULL period to
  `balance_calculator.calculate_balances`, which matches no anchor period
  (`app/services/balance_calculator.py:72`) and returns an empty dict
  (`:82-84`); the grid renders a blank balance row.
- **current-period-anchored projection seeded with the stored balance** --
  `/accounts` checking detail `app/routes/accounts.py:1418-1432` and `/savings`
  `app/services/savings_dashboard_service.py:325-352`: `... or
  (current_period.id ...)` falls back to the current period and seeds the
  engine with `current_anchor_balance or Decimal("0.00")`. Note this is NOT
  literally a "$0.00 anchor" as Q-16 worded it -- it is *whatever the stored
  balance is* (collapsing to $0.00 only when the column itself is 0/NULL).
- **account omitted entirely** -- dashboard `app/services/dashboard_service.py:683-684`
  and net worth `app/services/year_end_summary_service.py:2065-2066`:
  `return None`.
- **`$0.00`** -- calendar month-end `app/services/calendar_service.py:449-450`:
  `return Decimal("0")`.

Additional evidence Q-16 did not have: `scripts/integrity_check.py` BA-01
(`:292-297`) flags `(current_anchor_balance IS NOT NULL AND
current_anchor_period_id IS NULL)` as an anomaly -- i.e. the offline tooling
classifies the literal default new-user state as invalid, directly
contradicting the runtime fallbacks that treat it as a routine projection
input. Two readings (auditor does not choose):

- Interpretation A (`NULL` = "no usable balance yet"): omit/blank is correct;
  the `/accounts`+`/savings` current-period fallback
  (`accounts.py:1419-1421`, `savings_dashboard_service.py:326-328`) is the bug
  that fabricates a projection from an unset anchor. integrity_check BA-01
  leans this way.
- Interpretation B (`NULL` = "anchor at current period, balance as stored"):
  the fallback is correct; the omit/blank/`$0` paths are the bug that hides an
  account the user created and set a balance on.

Why it matters: classifies `current_anchor_period_id` (Phase 4
`04_source_of_truth.md` Family A, currently UNCLEAR) and governs the
SCOPE_DRIFT axis in F-001/F-003/F-006/F-007/W-277. Where it came up:
`docs/audits/financial_calculations/04_source_of_truth.md` Family A;
`app/services/auth_service.py:781-786`, `app/routes/grid.py:239-241`,
`app/routes/accounts.py:1418-1432`, `app/services/savings_dashboard_service.py:325-352`,
`app/services/dashboard_service.py:683-684`,
`app/services/year_end_summary_service.py:2065-2066`,
`app/services/calendar_service.py:449-450`, `scripts/integrity_check.py:292-297`.
Cross-link Q-16.

A-20 (developer, 2026-05-18): Resolved by E-19 (`00_priors.md` §0.3). Interpretation A
(`NULL` = no usable balance) and Interpretation B (`NULL` = anchor at current period with stored
balance) are both rejected: each presupposes a `NULL` state that has to be interpreted. Under E-19
the state is unreachable -- the anchor is a dated event from account creation, the period is
resolved on read, and `current_anchor_period_id` ceases to be an authoritative stored column. The
`integrity_check.py` BA-01 contradiction (it flags the universal default new-user state as an
anomaly, proven at `:294-296`) is removed because no account is ever in the anchor-set / period-NULL
shape. The auditor's "NOT literally a `$0.00` anchor -- it is whatever the stored balance is"
refinement is acknowledged and moot under E-19 (the stored balance is the t0 anchor, NOT NULL, no
range CHECK, overdraft-legal). Classifies `current_anchor_period_id` in `04_source_of_truth.md`
Family A as DERIVED (resolver-computed, non-authoritative), not UNCLEAR. Consistent with A-16/A-21.

Q-21 (P4-a, 2026-05-16) -- anchor stored/audit-mirror invariant, the create-path
history gap, the absent DB CHECK, and a Phase-1 inventory correction. Four
linked sub-questions surfaced by Phase 4 Family A; none has a documented intent:

1. Is "the latest `budget.account_anchor_history` row for an account mirrors
   `budget.accounts.current_anchor_balance`" a required invariant? The two are
   written together at `app/routes/accounts.py:462-467,796-801,1110-1115`, but
   `create_account` (`:321-332`), `auth_service.py:781-786`, and
   `seed_user.py:147` set the column with NO history row, and `update_account`
   /`inline_anchor_update` skip the history INSERT when no current pay period
   exists (`:460,794`) while still mutating the column. So the audit trail can
   permanently under-record. Should the mirror hold, and should it be enforced?
2. Should `create_account` / `auth_service` / `seed_user` emit a t0
   `AccountAnchorHistory` row? Today they do not, so
   `dashboard_service._get_last_anchor_date` (`:659-670`) returns None and the
   dashboard reports "Your checking balance has never been set."
   (`dashboard_service.py:276`) for an account whose balance IS set -- a
   stored-vs-counterpart mis-signal on the default account of every new user.
3. `budget.accounts.current_anchor_balance` and
   `budget.account_anchor_history.anchor_balance` have **no CHECK constraint**
   in the model (`app/models/account.py:51,152`) OR the migration
   (`migrations/versions/9dea99d4e33e_initial_schema.py:181,198`,
   bare `Numeric(12,2)`). `docs/coding-standards.md` requires a DB CHECK on
   every financial column, but a *checking* balance can legitimately be
   negative (overdraft), so a blanket `>= 0` may be wrong. What is the intended
   domain (any Decimal? `>= some floor`? unconstrained by design)? The auditor
   does not assume.
4. Phase-1 inventory correction (not editing `01_inventory.md` per protocol):
   its §1.5 `app/models/account.py` block records the CHECK for these two
   columns as "MIGRATION (not in model)" -- verified FALSE (no CHECK anywhere)
   -- and omits `current_anchor_period_id` from the column list entirely.
   Should `01_inventory.md` be corrected in a later reconciliation pass?

Why it matters: classifies `current_anchor_balance` (AUTHORITATIVE but with an
unenforced audit mirror) and `account_anchor_history.anchor_balance` (CACHED
with a reachable sync gap) in `04_source_of_truth.md` Family A; sub-question 2
is a live user-visible mis-alert on every new account. Where it came up:
`docs/audits/financial_calculations/04_source_of_truth.md` Family A;
`app/routes/accounts.py:321-332,460-467,794-801,1106-1115`,
`app/services/auth_service.py:781-786`, `scripts/seed_user.py:147`,
`app/services/dashboard_service.py:276,659-670`,
`app/models/account.py:51,152`,
`migrations/versions/9dea99d4e33e_initial_schema.py:181,198`,
`01_inventory.md` §1.5. Cross-link Q-20, Q-16, F-001.

A-21 (developer, 2026-05-18): Resolved by E-19 (`00_priors.md` §0.3), four sub-questions:

1. Audit-mirror invariant -- YES, it must hold and be enforced. The latest `AccountAnchorHistory`
   row always mirrors `current_anchor_balance` / `anchor_date`, written together in one transaction
   at t0 and on every anchor change; no creation path may set the anchor without emitting the
   history row. Classifies `current_anchor_balance` AUTHORITATIVE with an enforced (not unenforced)
   audit mirror; `account_anchor_history.anchor_balance` is the mirror, kept in sync by the same
   write, not a sync-gap CACHED column.
2. t0 history row on create -- YES. `create_account`, `auth_service.register_user`, and
   `seed_user` must emit the t0 `AccountAnchorHistory` row atomically with the account. This closes
   the live "Your checking balance has never been set." mis-signal
   (`dashboard_service.py:276`, proven: `_get_last_anchor_date:659-672` returns None only when no
   history row exists, which under E-19 cannot happen for a created account).
3. DB CHECK / intended domain -- DECIDED here (not deferred): `current_anchor_balance` and
   `account_anchor_history.anchor_balance` become NOT NULL with NO range CHECK, because a real
   checking balance can legitimately be negative (overdraft). The deliberate absence of a range
   CHECK is documented in a column comment per coding-standards' schema/DB-parity rule. (This is
   the one CHECK-domain item resolved in-cluster; Q-23 sub-3 and Q-24's CHECK items remain routed
   to the consolidated DB-constraint pass.)
4. Phase-1 §1.5 inventory miscite -- routed to the later reconciliation pass per the Q-21 sub-4 /
   Q-24 miscite-correction protocol (CHECK recorded as "MIGRATION (not in model)" is verified
   FALSE -- no CHECK anywhere; `current_anchor_period_id` omitted from the column list).
   `01_inventory.md` is NOT edited this session, consistent with the additive-only protocol.

F-001/F-003/F-006/F-007/W-277 stay findings; remediation target is the single date-anchored
resolver and the guaranteed atomic t0 anchor-plus-history write. Consistent with A-16/A-20.

Q-22 (P4-b1, 2026-05-16) -- **the `current_principal` source-of-truth role,
and a sharpening of Q-11 / Q-15 / Q-17.** Phase 4 Family B cannot classify
`budget.loan_params.current_principal` (it is recorded **UNCLEAR** in
`04_source_of_truth.md` Family B) without developer intent. The audit
determined definitively (re-run grep, full reads -- not inherited): **no code
path writes or recomputes `current_principal` when a transfer to a loan
settles.** Proof: `grep -rEn "\.current_principal\s*=[^=]" app/ scripts/`
returns zero attribute-write matches; the sole post-creation writer is the
manual `update_params` form (`app/routes/loan.py:674`,
`setattr(params, field, value)` gated by `_PARAM_FIELDS` at
`app/routes/loan.py:668-671`); and none of the 12 settle/status-transition
modules (`transfer_service.py`, `transaction_service.py`, `state_machine.py`,
`entry_service.py`, `credit_workflow.py`, `entry_credit_workflow.py`,
`carry_forward_service.py`, `recurrence_engine.py`, `transfer_recurrence.py`,
`routes/transactions.py`, `routes/transfers.py`, `routes/dashboard.py`) even
imports the `LoanParams` model. `transfer_service.update_transfer`
(`app/services/transfer_service.py:497-502`) propagates only status to the
parent and the two shadows; `transaction_service.settle_from_entries`
(`app/services/transaction_service.py:38-168`) writes only
status/paid_at/actual_amount and **rejects transfer shadows outright**
(`:111-115`). E-03 (`00_priors.md:172-176`) explicitly allows either "writing
a stored column" or "recomputing from confirmed payments"; the code does
**neither end-to-end** -- ARM reads the stored column everywhere and never
maintains it (`amortization_engine.py:978`,
`app/routes/debt_strategy.py:172-173`); fixed-rate recomputes via engine
walk on the refinance / debt-strategy / net-worth surfaces
(`amortization_engine.py:981-984`, `app/routes/debt_strategy.py:181-195`,
`app/services/year_end_summary_service.py:2078-2081`) but the prominent
`/accounts/<id>/loan` "Current Principal" card renders the stored column
regardless of loan type (`app/templates/loan/dashboard.html:104`, route passes
`params=params` at `app/routes/loan.py:553-557`; `proj` is computed at
`:429` but never wired to the card).

Two readings (auditor does not choose -- audit-plan section 9, hard rule 5):

- **Interpretation A -- AUTHORITATIVE:** the stored column is the intended
  source of truth and a settled transfer SHOULD reduce it by the principal
  portion (E-03's "writing a stored column"). Then the bug is the missing
  settle-driven update (F-014) plus, for fixed-rate, the engine-walk that
  diverges from the column. Classification -> AUTHORITATIVE with a
  SOURCE/SILENT drift finding.
- **Interpretation B -- CACHED:** the engine-real value is the truth and the
  stored column is a creation-time/manual seed that goes stale the instant a
  payment settles (E-03's "recomputing from confirmed payments", already done
  for fixed-rate on three surfaces). Then the bug is the dashboard card (and
  the `/savings` debt card, `app/services/savings_dashboard_service.py:840`)
  showing the stale mirror. Classification -> CACHED.

Sub-questions that sharpen the deferred questions, with both-side `path:line`
so the developer adjudicates without re-reading the code:

1. **Sharpens Q-11** (which principal the user-facing page MUST show). The
   divergence is now exact and proven: for a fixed-rate loan with any confirmed
   payment, the bold dashboard card shows STORED
   (`app/templates/loan/dashboard.html:104`) while the refinance prefill for
   the same loan-on-date shows engine-real
   (`app/routes/loan.py:1087` `current_real_principal = proj.current_balance`,
   `:1095`, `:1152`, `app/templates/loan/_refinance_results.html:69`) and the
   debt-strategy page shows a THIRD value (RAW-replay engine-real,
   `app/routes/debt_strategy.py:139,175-195`,
   `app/templates/debt_strategy/dashboard.html:132`). Worked example in
   `04_source_of_truth.md` Family B: `$200,000.00` / `$199,399.70` /
   `$198,495.20` for one loan-on-date, no error raised. Decide: render
   `proj.current_balance` on the dashboard, or relabel the card, or make all
   principal surfaces read one canonical resolver.
2. **Sharpens Q-15** (canonical aggregate-debt base). The F-008 internal
   inconsistency is now confirmed at source and holds **regardless of Q-15's
   answer**: inside one service, one loan, `_compute_account_projections` sets
   the account card's `current_balance` to `proj.current_balance`
   (`app/services/savings_dashboard_service.py:373`, engine) while
   `_compute_debt_summary` sums `lp.current_principal`
   (`:840` -> `:855` `total_debt += principal`, stored) -- the `/savings` page
   can show two different principals for the same loan. Q-15 governs which
   base is canonical for the aggregate; this sub-question asks additionally
   whether the within-`/savings` account-card-vs-debt-card mismatch is itself
   intended.
3. **Sharpens Q-17** (ARM re-amortization / symptom #4). Confirmed at source
   that the ARM monthly payment is re-amortized from the STORED column:
   `app/services/amortization_engine.py:951-953` computes
   `calculate_monthly_payment(current_principal, rate, remaining)` with
   `current_principal = Decimal(str(params.current_principal))`
   (`:913`) and `remaining = calculate_remaining_months(...)` (`:908-910`,
   strictly decreasing each month). Because `current_principal` is never
   reduced on settle (this Q's core finding), re-amortizing a non-decreasing
   principal over a shrinking `remaining` drifts the ARM payment upward
   monthly -- symptom #4 is the SAME un-maintained column as symptom #3. The
   two Q-17 interpretations (maintain the stored column on settle, vs.
   re-amortize the engine-real balance / honor the
   `arm_first_adjustment_months` window) map directly onto Interpretations A
   and B above.

Why it matters: classifies `current_principal` in `04_source_of_truth.md`
Family B (currently UNCLEAR); the answer also picks the symptom-#3 remediation
shape (F-014: maintain the column on settle, vs. F-016/Q-11: change the display
to the engine value) and ties symptoms #2/#3/#4/#5 to one column. E-01's escrow
rule is independently violated on the fixed-rate replay: `_compute_real_principal`
(`app/routes/debt_strategy.py:147-197`, read in full) passes RAW
`get_payment_history` (`:175`) into `generate_schedule` with NO
`prepare_payments_for_engine`, so the engine attributes the escrow portion to
principal paydown (`amortization_engine.py:531`
`principal_portion = total_payment - interest`); corroborated by A-06
verification (`09_open_questions.md:244-247`). That escrow-as-principal defect
is recorded as a Phase-3 SCOPE matter (F-014/F-017) and does not need a new
question -- A-06 already resolves that both pipeline layers must apply.

Where it came up: `docs/audits/financial_calculations/04_source_of_truth.md`
Family B (the two per-column findings, the settle-update trace, the worked
example). `app/models/loan_params.py:53-54`,
`app/routes/loan.py:553-557,622,668-679,1087,1095,1152`,
`app/routes/debt_strategy.py:119,139,147-197`,
`app/services/amortization_engine.py:908-984`,
`app/services/transfer_service.py:497-502`,
`app/services/transaction_service.py:38-168`,
`app/services/savings_dashboard_service.py:373,840,855`,
`app/services/year_end_summary_service.py:2078-2081`,
`app/templates/loan/dashboard.html:104`,
`app/schemas/validation.py:1438-1466`. Cross-link **Q-11**, **Q-15**,
**Q-17**, **A-04** / **A-05** / **A-06** (this file), **E-01** / **E-03**
(`00_priors.md:160-176`), **E-04** (`00_priors.md:178-182`), F-014, F-015,
F-016, F-008, F-017, loan side of F-001 / F-003.

A-22 (developer, 2026-05-18): Resolved by E-18 (`00_priors.md` §0.3). Interpretation A
(AUTHORITATIVE, maintain on settle) and Interpretation B (CACHED, display reads engine-real) are
both rejected as framed, because both presuppose a stored scalar that is either the truth or a
cache. `budget.loan_params.current_principal` is reclassified in `04_source_of_truth.md` Family B
as NEITHER AUTHORITATIVE NOR CACHED but RETIRED: it ceases to be an authoritative or hand-edited
column. The source of truth is the event stream (origination terms, confirmed payments, rate
changes, dated user-verified anchor events); one resolver derives balance, payment, and schedule on
read. Decisive proven constraint behind this choice: `amortization_engine.py:977-978` returns
`current_principal` verbatim for ARM, so a distinct engine-replayed value does not exist for ARM
(Interpretation B is vacuous for ARM); only event-derivation produces a decreasing ARM balance.
Sub-question 1 (Q-11) and sub-question 2 (Q-15 within-`/savings` mismatch) are resolved by every
surface reading the one resolver. Sub-question 3 (Q-17 symptom #4) closes because the resolver
amortizes the true event-derived balance and honors the fixed window. F-014/F-015/F-016/F-008/F-017
and the loan side of F-001/F-003 remain findings; the remediation target is the single resolver,
not maintenance of a mirror. Consistent with A-17 and A-23 (one stored-mirror policy).

Q-23 (P4-b2, 2026-05-16) -- **the `loan_params.interest_rate` source-of-truth
role, the effective-date-unaware mirror write, the missing `<= 1` DB CHECK,
and a sharpening of Q-17 with the engine source resolved so the developer can
adjudicate without re-reading the engine.** Phase 4 Family B classifies
`budget.loan_params.interest_rate` **UNCLEAR**. The audit determined
definitively (full reads of `amortization_engine.py`,
`loan_payment_service.py`, `loan.py`; tree-wide grep -- not inherited):

- **Rate authority is split by surface.** Every scalar `monthly_payment`
  display reads the STORED `loan_params.interest_rate`
  (`amortization_engine.py:914` -> ARM `:952-954`; `:957-959` fixed;
  `loan_payment_service.py:253,258`; `balance_calculator.py:216`;
  `loan.py:1227,1233`; `debt_strategy.py:110`;
  `savings_dashboard_service.py:478,845`;
  `year_end_summary_service.py:1473`). `RateHistory.interest_rate` is
  authoritative ONLY inside `generate_schedule`'s per-row loop via
  `_find_applicable_rate` (`amortization_engine.py:298-323`, fires at
  `:498-514` only when `rate_changes` is passed), and `rate_changes` is built
  ONLY by `load_loan_context:131-144` for ARM loans, consumed only by the
  loan-dashboard schedule (`loan.py:429-431`) and the year-end schedule
  (`year_end_summary_service.py:1470-1480`). So the bold "Monthly P&I" card
  (`loan/dashboard.html:129`, site 7) uses the stored mirror while the
  schedule on the same page (site 4) uses the RateHistory series.
- **The mirror is written effective-date-unaware.** `add_rate_change`
  (`loan.py:685-758`) INSERTs the RateHistory row (`:700-706`) and then
  unconditionally executes `params.interest_rate = data["interest_rate"]`
  (`:709`) with the just-submitted rate, regardless of its `effective_date`.
  Recording a future-effective change moves the displayed scalar payment NOW;
  recording a backdated correction after a later change leaves the mirror at
  the backdated value. Nothing reconciles
  `loan_params.interest_rate` to "the RateHistory row in effect today."
- **Symptom #4 is rate-independent inside the fixed window.** A 5/5 ARM has
  no RateHistory rows in its first 60 months, so the scalar and schedule
  agree on rate there; the in-window drift is purely the frozen stored
  `current_principal` (Q-22) re-amortized over the calendar-shrinking
  `calculate_remaining_months` (`amortization_engine.py:908-910,136-142`) at
  `:952-954`. Exact-Decimal worked example
  (`04_source_of_truth.md` Family B): `P=$400,000`, `r=6.000%`, `T=360`,
  fixed window 60 mo -- correct constant **$2,398.20**; engine returns
  **$2,400.59** (mo 1), **$2,460.50** (mo 24), **$2,573.51** (mo 59), a
  strictly upward creep, no rate change, no manual edit. Confirmed E-02
  violation. `arm_first_adjustment_months` / `arm_adjustment_interval_months`
  (`loan_params.py:60-61`) are stored, form-bound (`loan.py:670`),
  schema-validated (`validation.py:1450-1451,1471-1472`) and consumed by
  **zero** calculation sites (grep-proven) -- the engine has no fixed-window
  concept.

Three linked questions (auditor does not choose -- audit-plan section 9, hard
rule 5):

1. **Column role (sharpens Q-17, parallels Q-22).** Is
   `loan_params.interest_rate` intended to be **AUTHORITATIVE** (the
   user-maintained current rate; the bug is then that the schedule path uses
   a different RateHistory-resolved rate and that the scalar payment ignores
   the fixed-window columns -> remediate at the engine), or **CACHED** (a
   denormalized mirror of the latest RateHistory entry; the bug is then that
   every scalar display reads the stale mirror instead of resolving
   `_find_applicable_rate(today, ...)` from RateHistory)? This is the same
   fork as Q-17 (hold the payment constant via the stored anchor vs.
   recompute from the authoritative series) and Q-22 (the `current_principal`
   role); the answers should be consistent across all three.
2. **Effective-date-unaware mirror write.** Is `add_rate_change:709`
   overwriting `params.interest_rate` with the submitted rate regardless of
   `effective_date` the intended behavior, or must the mirror equal the
   RateHistory rate in effect *today* (i.e. the write should resolve via
   effective-date, and future-dated changes should not move the displayed
   payment until they take effect)?
3. **Intended numeric domain / missing DB CHECK.**
   `loan_params.interest_rate` DB CHECK is `>= 0` with **no upper bound**
   (`loan_params.py:35-38`, migration `dc46e02d15b4:32`), whereas its own
   audit mirror `rate_history.interest_rate` has CHECK `>= 0 AND <= 1`
   (`loan_features.py:44-47`) and `add_rate_change` writes the same value to
   both. The Marshmallow schemas bound the *percent* input to `0-100`
   (`validation.py:1445,1467`) but that is not a DB constraint. A checking
   *rate* `> 100%` is implausible, but `docs/coding-standards.md` requires
   schema/DB range parity and the auditor does not assume the intended
   domain. What is the intended DB-enforced domain for
   `loan_params.interest_rate` (mirror its `rate_history` sibling at
   `<= 1`? a different ceiling? intentionally unconstrained)?

Why it matters: classifies `loan_params.interest_rate`
(`04_source_of_truth.md` Family B, currently UNCLEAR); governs the
remediation shape for F-013 (symptom #2) and F-026 (symptom #4) jointly with
Q-17/Q-22 (the answers must agree -- symptoms #2/#3/#4 are one
un-maintained-stored-column family); sub-question 2 is a live user-visible
mis-display whenever a future-dated or out-of-order rate change is entered;
sub-question 3 is an unenforced-domain gap analogous to Q-21 sub-question 3
(Family A's missing anchor CHECK). The verdict (DIVERGE for F-013/F-026) is
not blocked; only the fix shape is.

Where it came up: `docs/audits/financial_calculations/04_source_of_truth.md`
Family B (the three per-column findings, the symptom-#4/E-02/Q-17 crux
subsection, the worked example, the entry-point matrix).
`app/services/amortization_engine.py:298-323,498-514,908-910,913-914,
936,950-959`, `app/services/loan_payment_service.py:121-144,247-260,263-353`,
`app/routes/loan.py:340-389,399-402,429-431,665-674,698-709,1222-1234`,
`app/routes/debt_strategy.py:110,127-129,147-197`,
`app/services/savings_dashboard_service.py:478,845-857`,
`app/services/year_end_summary_service.py:1470-1480`,
`app/models/loan_params.py:35-38,55,60-61`,
`app/models/loan_features.py:44-47,75,103-106,126`,
`app/schemas/validation.py:1445,1467,1484,1496`,
`migrations/versions/dc46e02d15b4_add_check_constraints_to_loan_params_.py:32`,
`migrations/versions/b71c4a8f5d3e_c24_marshmallow_range_check_sweep.py:109-110,201-202`.
Cross-link **Q-17** (`09_open_questions.md:699-740`, this Q resolves its
engine-source half), **Q-22** (`:916-1035`, the `current_principal`
sibling -- same fork, answers must agree), **Q-11** / **Q-15**,
**E-02** (`00_priors.md:166-170`), **E-01** / **E-03**
(`00_priors.md:160-176`), F-013, F-026, F-019, F-014, F-017.

A-23 (developer, 2026-05-18): Resolved by E-18 (`00_priors.md` §0.3), consistent with A-22 and A-17
(the P4-e addendum required these be answered as one policy).

- Sub-question 1 (column role): `loan_params.interest_rate` is neither AUTHORITATIVE nor a CACHED
  mirror; like `current_principal` it is RETIRED as a hand-maintained scalar. The authoritative
  rate series is `RateHistory`; the rate in effect on any date is resolved from that series by the
  single resolver, and every scalar display reads the resolver, not a denormalized mirror. (The
  rate is not perfectly symmetric with principal -- it has its own real authoritative series rather
  than being replayed from payment events -- but the policy is the same: derive on read from the
  event/series, never hand-maintain a mirror.)
- Sub-question 2 (effective-date-unaware mirror write at `add_rate_change:709`): moot once the
  scalar mirror is retired. There is no `params.interest_rate` to overwrite; `add_rate_change`
  inserts the `RateHistory` row (the event) and the resolver picks the row in effect on the
  evaluation date. Future-dated and out-of-order changes therefore no longer move the displayed
  payment until they take effect, which is the correct behavior.
- Sub-question 3 (missing DB CHECK domain): this is an unenforced-domain question independent of
  the source-of-truth policy and is NOT decided by E-18. Routed to the Phase-6 / DB-constraint
  pass together with Q-21 sub-3 and Q-24's CHECK-domain items; the developer will set the intended
  DB-enforced ceiling for any rate column retained as input (mirror the `rate_history` sibling at
  `<= 1`, a different ceiling, or intentionally unconstrained) in that consolidated pass.
  RESOLVED 2026-05-18 by E-28 (`00_priors.md` §0.3): mirror the sibling --
  `loan_params.interest_rate` CHECK becomes `interest_rate >= 0 AND interest_rate <= 1`,
  identical to `rate_history.interest_rate`; schema percent input stays `Range(0,100)`,
  stored as the 0..1 fraction. (Note: under A-23 sub-1, `loan_params.interest_rate` is RETIRED
  as a hand-maintained mirror; the CHECK parity still applies for any retained input column and
  for `rate_history` as the authoritative series.)

F-013/F-026/F-019/F-014/F-017 remain findings; remediation target is the single resolver.

---

Q-24 (P4-c, 2026-05-16) -- **Family C (Interest/Investment params): a Phase-3
miscite to correct, plus three stored-input "0 vs None" intent questions the
developer must adjudicate.** Phase 4 Family C classified all six columns
AUTHORITATIVE and proved no cached projected-balance column exists. Three
items need a developer decision:

1. **`contribution_limit_remaining` is cited to code that does not exist.**
   `02_concepts.md:2169-2200` and `03_consistency.md` **F-044** state the
   `annual_contribution_limit - ytd_contributions` "remaining" subtraction is
   "route-resident at `app/routes/investment.py:173-181` (`limit_info`)" and
   give F-044 verdict AGREE / single-path. Verified at source this session:
   `investment.py:173-181` is the `calculate_investment_inputs(...)` call;
   `limit_info` is built at `investment.py:230-238` and is
   `{"limit", "ytd", "pct"}` with **no `limit - ytd` subtraction**; the
   template (`app/templates/investment/dashboard.html:76,88`) renders
   "`ytd / limit`" and a percent-used bar only. A "remaining" figure is
   **never computed or displayed anywhere.** Question: is a contribution-
   limit *remaining* figure intended to be shown (in which case it is
   unimplemented and F-044's AGREE is moot), or is the concept-catalog entry
   an over-specification of a concept the app deliberately expresses as
   percent-used? (Audit will revise F-044 / the `02_concepts.md` entry per
   the answer; `01`/`02`/`03` left unedited per protocol -- `01_inventory.md`
   §1.2 is itself accurate.)
2. **A blank `apy` (or `assumed_annual_return`) on a first save silently
   becomes 4.5% (or 7%), not zero and not an error.** The interest handler
   binds `InterestParamsUpdateSchema` (`app/routes/accounts.py:64`) in which
   `apy` is **not `required`** and blanks are stripped
   (`app/schemas/validation.py:1393-1395,1414`); with the `if not params:`
   create branch (`accounts.py:1356-1363`) a first save omitting `apy`
   commits a row whose `server_default="0.04500"`
   (`app/models/interest_params.py:60`) yields a silent 4.5% APY that
   projects real interest the user never set (`calculate_interest` treats
   only `apy <= 0` as no-interest, `interest_projection.py:83`).
   `InvestmentParams.assumed_annual_return` carries the symmetric
   `server_default="0.07000"` plus a **float** Python `default=0.07000`
   (`investment_params.py:81`, a coding-standards "construct Decimals from
   strings" violation). Question: is a blank-rate first save a reachable UI
   flow, and should a missing rate be a validation error (or an explicit 0)
   rather than a silent plausible-looking default?
3. **A stored `annual_contribution_limit = 0` means three different things on
   one app.** `investment.py:231` truthiness -> limit card suppressed ("no
   limit"); `investment.py:667` truthiness -> contribution-transfer default
   falls to the `$500.00` literal ("no limit"); `growth_engine.py:206`
   `is not None` -> absolute zero cap (no contribution ever counts). The
   CHECK permits 0 (`investment_params.py:31-35`,
   `... IS NULL OR ... >= 0`). Question: is `annual_contribution_limit = 0` a
   meaningful user state ("contributions disallowed"), and if so which
   interpretation is correct -- or should 0 be normalised to NULL / rejected
   at the schema tier?

Why it matters: sub-question 1 governs whether F-044's AGREE stands or the
concept is unimplemented (affects the `02_concepts.md` /
`03_consistency.md` F-044 entries and any Phase-7 test for
`contribution_limit_remaining`); sub-questions 2-3 are live coding-standards
"`0` and `None` mean different things" hazards on AUTHORITATIVE stored input
columns -- 2 is a silent wrong-projection on a first save, 3 is an E-04-class
cross-consumer divergence (`04_source_of_truth.md` Family C,
`budget.investment_params.annual_contribution_limit`). None blocks a Family C
verdict (all six columns are AUTHORITATIVE regardless); only the F-044
disposition and the remediation shape for 2-3 depend on the answers. The
zero-`assumed_annual_return` read-path drop at
`retirement_dashboard_service.py:321` is already owned by F-042 (no new
question -- cross-link only).

Where it came up:
`docs/audits/financial_calculations/04_source_of_truth.md` Family C (the
cached-balance determination, the six per-column findings, the
consumer-routing section, and the Phase-3 re-verification log).
`app/routes/investment.py:173-181,185-189,230-238,667-670`,
`app/templates/investment/dashboard.html:70-88`,
`app/routes/accounts.py:64,1349-1367`,
`app/schemas/validation.py:1393-1397,1414`,
`app/models/interest_params.py:33-36,60`,
`app/models/investment_params.py:21-24,31-35,80-83`,
`app/services/investment_projection.py:169-171,175-190`,
`app/services/growth_engine.py:206-209`,
`app/services/retirement_dashboard_service.py:321`,
`02_concepts.md:2169-2200`, `03_consistency.md` F-044.
Cross-link **F-042** (`09`/`03`; the zero-`assumed_annual_return` slider drop
and SWR `or "0.04"` SILENT_DRIFT -- this Q does not re-raise it),
**F-041** (`apy_interest` single engine, AGREE), **F-044** / **F-045**,
**Q-21** (the Phase-1/Phase-3 miscite-correction protocol this follows;
Family C's §1.5 model blocks are by contrast ACCURATE),
**E-04** (`00_priors.md:178`).

A-24 (developer, 2026-05-18): Resolved by E-28 (`00_priors.md` §0.3), the consolidated DB-CHECK
pass.

- Sub-question 1 (`contribution_limit_remaining` F-044 miscite): documentation-only; the concept
  is genuinely unimplemented (the app expresses it as percent-used, no `limit - ytd`). NOT a code
  decision; routed to the reconciliation pass per the Q-21 sub-4 / Q-24 miscite-correction
  protocol. F-044's AGREE is moot; `02_concepts.md` / `03_consistency.md` revised there, `01`
  unedited per the additive-only protocol.
- Sub-question 2 (blank `apy` / `assumed_annual_return` first save -> silent server_default):
  PROVEN reachable this session (handler binds `InterestParamsUpdateSchema` for the create branch,
  `accounts.py:64,1349-1366`; the dedicated required-rate `InterestParamsCreateSchema` is unused).
  Resolved: rate is REQUIRED on the create path (clean 400, no silent default); `server_default`
  kept as DB safety net only. The `investment_params.py:81` float `default=0.07000` is a
  coding-standards "construct Decimals from strings" finding, fixed as part of the change (not an
  intent question).
- Sub-question 3 (`annual_contribution_limit = 0` three-way divergence): PROVEN (truthiness at
  `investment.py:231,667` = "no limit"; `is not None` at `growth_engine.py:206` = hard zero cap).
  Resolved: normalise `0 -> NULL` at the Marshmallow tier; states are NULL = "no limit" and
  `> 0` = "a real limit"; CHECK becomes `IS NULL OR > 0`; all consumers use `is not None`. No
  "0 = disallowed" state. The CHECK narrowing is a destructive migration (review line + migrate
  existing `= 0` rows to NULL first + verifying SELECT).

Q-25 (P4-d, 2026-05-16) -- **Family D: the calibration `effective_*_rate`
columns are UNCLEAR (the audit cannot classify them without an intent
decision), plus a secondary savings-goal plan-vs-need reconciliation
question.** Two items need a developer decision:

1. **Is a saved `CalibrationOverride` a frozen pay-stub snapshot, or a live
   derived rate?** `effective_federal_rate`/`effective_state_rate`/
   `effective_ss_rate`/`effective_medicare_rate`
   (`app/models/calibration_override.py:89-92`) are computed from the same
   row's `actual_*` columns plus the profile's pre-tax-deduction total by
   `derive_effective_rates`@`app/services/calibration_service.py:83-96`, but
   persisted by a path that never re-derives them: `calibrate_preview`
   computes them (`app/routes/salary.py:1105-1112`), ships them as hidden
   form inputs (`app/templates/salary/calibrate_confirm.html:97-100`), and
   `calibrate_confirm` stores `effective_*_rate=data["effective_*_rate"]`
   straight from the POST (`app/routes/salary.py:1161-1164`) independently of
   the separately-posted `actual_*` (`:1156-1160`), with only a `[0,1]`
   range check (`app/schemas/validation.py:1858-1873`) and no cross-check
   that `effective_x == actual_x / base`. `apply_calibration`@
   `app/services/calibration_service.py:133-144` then multiplies the stored
   rate against the **live** per-period taxable/gross every calibrated
   paycheck. Two silent drift surfaces follow: (a) a tampered/replayed/stale
   two-step POST stores a rate pair inconsistent with the actual_* pair;
   (b) editing the profile's pre-tax deductions or salary after save does
   not recompute the rates (the only writer is calibrate_confirm), so the
   stored federal/state rate is derived against a now-stale taxable base.
   Question: is the calibration intended to be an immutable pay-stub
   snapshot (then the actual_*-vs-rate inconsistency window and the absence
   of a derive-at-confirm step are the defect, and the column is
   AUTHORITATIVE-snapshot), or a live derived rate (then the missing
   recompute on profile/deduction edit is the defect, and the column is
   DERIVED-stale)? The auditor does not pick a side (hard rule 5 / Phase-4
   decision 2); the classification stays **UNCLEAR** until answered.
2. **Are `savings_goals.contribution_per_period` (the user's planned
   contribution) and the dashboard's computed `required_contribution` (the
   contribution needed to hit the target by date) meant to be reconciled?**
   `contribution_per_period` is a pure user input -- no service writes it
   (tree-wide grep; `app/routes/savings.py:143,236`,
   `app/models/savings_goal.py:77`); `calculate_required_contribution`@
   `app/services/savings_goal_service.py:109-136` produces a separate,
   never-persisted figure surfaced beside it on `/savings`
   (`app/services/savings_dashboard_service.py:676-678,717`;
   `app/templates/savings/dashboard.html:411-414`). The code never validates
   or warns when the stored plan is below the computed need (a user can
   store $50/period while the dashboard says $400/period is required).
   Question: is this an intentional plan-vs-actual display, or should the
   stored value be validated/flagged against the computed requirement?

Why it matters: sub-question 1 is the only item in Family D that blocks a
column classification -- the four `effective_*_rate` columns feed every
calibrated federal/state/FICA withholding projection
(`apply_calibration`@`calibration_service.py:133-144`,
`paycheck_calculator.py:160-167`), so a stale or inconsistent stored rate is
a silent wrong-paycheck-projection with no error; the remediation shape
(re-derive at confirm + recompute-on-profile-edit, vs lock the snapshot)
depends entirely on the intended contract. Sub-question 2 does not block any
classification (`contribution_per_period` is AUTHORITATIVE regardless) and is
a UX/validation-intent question only. Neither blocks the other Family-D
verdicts (all ~40 triage columns and `transactions.actual_amount` /
`savings_goals.contribution_per_period` are AUTHORITATIVE regardless).

Where it came up:
`docs/audits/financial_calculations/04_source_of_truth.md` Family D
(Escalation 3 -- the UNCLEAR `effective_*_rate` group finding; Escalation 2 --
the `contribution_per_period` finding).
`app/models/calibration_override.py:53-68,89-92`,
`app/services/calibration_service.py:34-103,106-145`,
`app/routes/salary.py:1064-1176`,
`app/templates/salary/calibrate_confirm.html:97-100`,
`app/schemas/validation.py:1858-1873`,
`app/services/paycheck_calculator.py:160-167`,
`app/services/savings_goal_service.py:109-136`,
`app/routes/savings.py:37,143,236`,
`app/services/savings_dashboard_service.py:665-722`,
`app/templates/savings/dashboard.html:411-414`.
Cross-link **F-035** (`federal_tax` bracket-vs-calibrated gated AGREE),
**F-037** (`fica` calibration-path SS-cap bypass DEFINITION_DRIFT -- the same
calibrated path consuming these rate columns), **F-046** (`goal_progress`
GP1, the savings-goal producers in sub-question 2),
**Q-13** (`salary.calibrate_preview` taxable derivation),
**Q-21** (the Phase-1/Phase-3 miscite-correction protocol; Family D's §1.5
model blocks are by contrast ACCURATE, consistent with Family C).

A-25 (developer, 2026-05-18):

- Sub-question 1 (the `effective_*_rate` calibration columns -- frozen snapshot vs live derived
  rate): Resolved by E-18 (`00_priors.md` §0.3), consistent with A-17/A-22/A-23 (the P4-e addendum
  required this be answered as one stored-mirror policy). A hand-maintained scalar that mirrors a
  computation is never the source of truth. The intended contract is the immutable-snapshot reading:
  a `CalibrationOverride` is a frozen pay-stub snapshot whose deriving inputs are captured at
  capture time, so it is AUTHORITATIVE-snapshot, not DERIVED-stale. The defects are therefore (a)
  the actual_*-vs-rate inconsistency window (the rates must be derived at confirm from the same
  posted actual_* values, not accepted as independent hidden form inputs) and (b) the absence of a
  derive-at-confirm step; these are the remediation, recorded as findings, classification UNCLEAR ->
  AUTHORITATIVE-snapshot in `04_source_of_truth.md` Family D. (Editing the profile/deductions after
  save does NOT silently re-derive a stored rate, by design -- the snapshot is immutable; a new
  calibration is a new snapshot event.) Sharpened 2026-05-18 by E-20 (`00_priors.md` §0.3) and
  A-13: the concrete remediation is a new `actual_pre_tax_deductions` pay-stub field so the taxable
  base is 100% pay-stub-grounded (`taxable = actual_gross_pay - actual_pre_tax_deductions`), plus
  server-side derive-at-confirm of the four effective rates from the posted `actual_*` values. This
  also fixes the proven Q-13 profile-gross contamination, which is the same remediation surface.
- Sub-question 2 (`savings_goals.contribution_per_period` planned vs computed
  `required_contribution`): This is a UX/validation-intent question independent of E-18 and is NOT
  decided by it. Intentional plan-vs-actual display is acceptable; the stored value is not forced to
  match the computed requirement. RESOLVED (developer, 2026-05-18): a non-blocking advisory is
  surfaced on `/savings` when `contribution_per_period` is below the computed
  `required_contribution` (clear indicator that the goal reaches completion after the target
  date, with the required figure shown); saving is never blocked and the input is never
  auto-changed. `contribution_per_period` stays AUTHORITATIVE -- this applies the E-24 principle
  ("a committed user amount is real; surface the delta, never override") at the display layer,
  the same shape as E-21. Not formalised as a separate E-expectation (a non-blocking display
  advisory, not a calculation invariant; formalising it would be gold-plating). Phase 3 / 5
  verification direction: the absence of any plan-vs-required signal is a UX finding; remediation
  is the advisory only, no validation hard-stop, no classification change
  (`contribution_per_period` remains AUTHORITATIVE).

---

P4-e cross-link addendum to Q-25 (2026-05-16) -- **not a new question; a
missing cross-link the Phase-4 consolidation surfaced.** Q-25's
`effective_*_rate` AUTHORITATIVE-snapshot-vs-DERIVED-stale fork is, per
`04_source_of_truth.md` Phase 4 deliverable 5 (headline consolidation), a
**facet of the same structural decision** as **Q-17** (ARM re-amortization
source), **Q-22** (`current_principal` role), and **Q-23**
(`loan_params.interest_rate` role): *is a stored column that mirrors/anchors a
computation AUTHORITATIVE (bug = missing maintenance-on-event) or
CACHED/DERIVED (bug = display reads the stale mirror)?* The
`effective_*_rate` columns are written once from a client snapshot at
`calibrate_confirm` and never re-derived on the triggering event (profile /
pre-tax-deduction edit) -- structurally identical to `current_principal`
written once and never re-derived on settle. Q-25's existing cross-links
(F-035, F-037, F-046, Q-13, Q-21) did not reach Q-17/Q-22/Q-23, so the
coupling was not discoverable from Q-25 alone; this addendum supplies it. The
developer should answer Q-17/Q-22/Q-23/Q-25 as **one stored-mirror-maintenance
policy**, not piecemeal. Cross-link **Q-17**, **Q-22**, **Q-23**, **Q-26**.

Q-26 (P4-e, 2026-05-16) -- **the `auth.user_settings.estimated_retirement_tax_rate`
Phase-4 coverage GAP, and its intended source-of-truth role.** The Phase-4
completeness reconciliation (`04_source_of_truth.md` Phase 4 deliverable 2,
finding **F-046-SoT**) found this column is **absent from every Family section
of `04_source_of_truth.md`** (`grep -n estimated_retirement_tax_rate
04_source_of_truth.md` -> zero matches before this session's consolidation):
no family classifies it, and the Family D triage table (`04:1841-1883`) omits
it while listing 5 of the 6 `UserSettings` rate/threshold columns -- so the
triage table's closing completeness claim (`04:1885-1886`, "No stored-monetary
column outside this list was found during the per-column greps") is
**inaccurate**. The column is `Numeric(5,4)` nullable, CHECK at
`app/models/user.py:216` (defined `:242`), `01_inventory.md:731` concept token
`federal_tax (retirement projection input)`, consumed as a `federal_tax`
producer input by the retirement gap analysis
(`02_concepts.md:2875` lists `UserSettings.estimated_retirement_tax_rate@user.py:242`),
and is "one of the rate fields inspected by PA-02" (`01_inventory.md:739`). It
is money-affecting: it multiplies projected retirement income to a withholding
figure.

Two readings (auditor does not choose -- audit-plan section 9, hard rule 5):

- **Interpretation A -- AUTHORITATIVE:** a pure user-setting input with no
  service writer (the expected disposition by the Family D triage table's own
  rule, "AUTHORITATIVE unless a service computes+stores it"). The defect is
  then only the Phase-4 omission itself (and the inaccurate `04:1885-1886`
  completeness claim), and the remediation is a documentation pass: run the
  confirmatory `grep -rn '\.estimated_retirement_tax_rate\s*=' app/services/`
  that backs every other triage row but was never run for this column, then
  add the per-column row + classification line.
- **Interpretation B -- CACHED/DERIVED:** some path derives, recomputes, or
  stores it (e.g. from a profile or a prior projection), giving it a staleness
  surface analogous to the Q-25 `effective_*_rate` columns. Then it belongs to
  the headline stored-mirror-maintenance family (Q-17/Q-22/Q-23/Q-25) and the
  defect is the missing re-derivation on the triggering edit.

The auditor does not assert the verdict (the confirmatory write-grep that
backs every other triage row was not run/recorded for this column); the
classification stays **UNCLEAR / GAP** until the developer confirms the role.

Why it matters: it is the **single Phase-4 coverage GAP** -- every other
stored-monetary §1.5 column is covered or escalation-handled. The column feeds
every retirement federal-tax projection (`02_concepts.md:2875`,
`paycheck_calculator`/`retirement_gap_calculator` chain), so a mis-classified
role or an undetected staleness is a silent wrong-projection with no error.
Sub-point: the Family D triage completeness claim at `04:1885-1886` should be
corrected in a later reconciliation pass (consistent with the Q-21 sub-q4 /
Q-24 miscite-correction protocol; no edit to the Family D section this
session, per the additive-only Phase-4 protocol). Resolving this also closes
the last open `04` acceptance-gate item (deliverable 6 criterion b: PASS with
this one recorded GAP).

Where it came up: `docs/audits/financial_calculations/04_source_of_truth.md`
Phase 4 deliverable 2 (F-046-SoT) and the consolidated classification table
(deliverable 3). `app/models/user.py:216,242`, `01_inventory.md:731,739`,
`02_concepts.md:2875`. Cross-link **Q-21** / **Q-24** (the Phase-1/Phase-3
miscite-correction protocol this follows), **F-042** (the other `UserSettings`
rate read-path 0-vs-None hazards -- `safe_withdrawal_rate`,
`trend_alert_threshold`; this Q does not re-raise those), **PA-02**
(`00_priors.md` section 0.6, the prior-audit rate-field finding),
**Q-17**/**Q-22**/**Q-23**/**Q-25** (the stored-mirror-maintenance family, if
Interpretation B holds), **E-04** (`00_priors.md:178`).

--- P4-f ANNOTATION (2026-05-16; question NOT deleted, see hard rule 5) ---

**Source-of-truth-role sub-question: RESOLVED -> AUTHORITATIVE.** P4-f
(`04_source_of_truth.md` "Phase 4 - P4-f gap closure", finding F-046-SoT,
now CLOSED) settled the Q-26 A-vs-B fork mechanically, not by auditor
preference: the confirmatory `grep -rn 'estimated_retirement_tax_rate\s*='
app/services/` returns zero matches (exit 1) -- no service computes-and-
stores it; the single write path is user-driven
(`app/routes/retirement.py:338-410 update_settings`); the single
computational consumer reads it
(`app/services/retirement_dashboard_service.py:222-226`); nothing derives
into it. Interpretation A (AUTHORITATIVE, pure user input, no staleness
surface) holds; Interpretation B (CACHED/DERIVED) is disproven. The column
is **AUTHORITATIVE**; the GAP is closed; it is no longer the open Phase-4
coverage gap. Acceptance-gate 6(b) now PASSes on an independently model-
derived 62-column denominator (P4-f Deliverable 2), superseding the P4-e
§1.5-based reconciliation (`04:2040-2047`); the model-derived census
surfaced **zero new gaps**, so no new open questions are raised by the
census.

**NEW sharpened sub-question (developer adjudicates; auditor does not pick
a side):** When `estimated_retirement_tax_rate` is **unset (NULL)**, what
is the intended behavior?

- Code behavior: `retirement_dashboard_service.compute_gap_data:222-226`
  passes `None` to `retirement_gap_calculator.calculate_gap`, whose
  `estimated_tax_rate is not None` guards (`:76`, `:108`) then **skip the
  entire after-tax computation** -- the projection uses **gross/untaxed**
  retirement income (`:85` falls back to gross `monthly_pension_income`;
  `after_tax_*` fields stay `None`). There is **no bracket-based estimate
  anywhere in `calculate_gap` (full read, `:37-136`)**.
- Documented behavior: the model comment
  `app/models/user.py:215-216` states NULL means "fall back to current
  bracket-based estimate".

These diverge silently in every retirement projection of a user who has
not set the field (untaxed over-optimistic figure vs a bracket-based
estimate). Which is correct: should `calculate_gap` gain a bracket-based
fallback to match the model comment, or should the model comment be
corrected to "NULL = no retirement-tax adjustment applied" to match the
code? Why it matters: this is now the live defect behind F-046-SoT
(the source-of-truth *classification* is settled; the *NULL-semantics
contract* is not). Cites: `app/models/user.py:215-216` (doc),
`app/services/retirement_gap_calculator.py:43,76,85,108` (code),
`app/services/retirement_dashboard_service.py:222-226` (the bridge).

**Secondary (route to Phase-3/Phase-6 with the F-042 family, not adjudicated
here):** the read guard `if settings and
settings.estimated_retirement_tax_rate`
(`app/services/retirement_dashboard_service.py:224`) is a truthiness test;
an explicit user `Decimal("0.0000")` (CHECK admits `>= 0`; semantically
distinct from NULL "unset" per the model comment) is coerced to `None`,
suppressing the after-tax dataclass fields. Violates coding-standards.md
"Do not rely on truthiness for business logic". Same shape as **F-042**
(`safe_withdrawal_rate`/`trend_alert_threshold` 0-vs-None); recorded, not
re-litigated under Q-26.

**PA-02 status (for completeness):** PA-02's percent-Range-vs-decimal-CHECK
hazard does **not** hold for this column in current source --
`RetirementSettingsSchema` uses `Range(0,1)`
(`app/schemas/validation.py:1752-1755`), the route `/100`-normalizes before
validation (`app/routes/retirement.py:348-351`), DB CHECK is `0..1`
(`user.py:217-219`); remediated by C-24/F-077. `01_inventory.md:739`'s
"one of the rate fields inspected by PA-02" is an over-broad linkage and is
moot. Full reasoning in `04_source_of_truth.md` finding F-046-SoT.

A-26 (developer, 2026-05-18): The source-of-truth-role half is already CLOSED (AUTHORITATIVE, P4-f
annotation above) and is consistent with E-18 (`00_priors.md` §0.3): a pure user input with no
mirror has no staleness surface, so it is not part of the stored-mirror-retirement family of
A-17/A-22/A-23/A-25. E-18 does NOT decide the remaining open item -- the NULL-semantics contract --
which is a model-comment-vs-code divergence, not a source-of-truth question. Decision on that
sub-question: correct the model comment at `app/models/user.py:215-216` to read "NULL = no
retirement-tax adjustment applied" so it matches the code (`calculate_gap` has no bracket-based
fallback, full read `:37-136`); do NOT add a bracket-based fallback. Rationale: a silent
bracket-based estimate the user never set is itself a hidden-default hazard of exactly the kind
E-18 and coding-standards "0 and None mean different things" exist to prevent; an explicit, visible
"no retirement-tax adjustment applied (set an estimated rate to model withholding)" is the honest
behavior. This is now an F-046-SoT documentation-correction finding. The secondary truthiness guard
(`retirement_dashboard_service.py:224` coercing an explicit `Decimal("0.0000")` to None) stays
routed to the F-042 family as already recorded; not re-litigated here.

---

## Phase 9 close -- open-question completeness, carried-item reconciliation, audit-level acceptance (2026-05-19)

Phase 9 deliverable per `financial_calculation_audit_plan.md` section 9 (this
file holds every "what is the intended behavior here?" the audit raised) and
section 11 (the eight audit-level acceptance criteria). This is the audit's
single completion capstone: Phases 0-8 each closed with their own gate
roll-up; the audit as a whole was never formally certified. This section does
that and stops.

Discipline: additive only -- no Q/A text above is rewritten; the point-in-time
record stands and is certified here. Trust-but-verify applied to the audit's
own output: the one carried item is re-derived from live source this session,
not inherited from the Phase-8 handoff. No source, test, migration, template,
or static file is touched (acceptance criterion 7, re-proven in Part C/E).

### Part A -- open-question completeness

- Q-01 .. Q-26 present; each carries a developer `A-NN`. Q/A id parity 26/26
  (`grep -oE '^Q-[0-9]+' | sort -u` vs `^A-[0-9]+ \(developer`, both yield the
  identical 01..26 set).
- Zero `STILL OPEN` / `UNRESOLVED` / `NOT ANSWERED` markers anywhere in the
  file.
- Q-01..Q-07 answered 2026-05-13; Q-08..Q-26 answered 2026-05-18. All locked
  as behavioral expectations E-01..E-28 in `00_priors.md` section 0.3 and
  reconciled back into the gated phase outputs by the dedicated reconciliation
  passes (commits `06c26b1`, `63c7800`, `c7fb790`, `2de5dd6`; reconciliation
  sections at the tails of `02_concepts.md`, `03_consistency.md`,
  `04_source_of_truth.md`).
- No new ambiguity surfaced by Phases 5-8 (each phase's G8 attested "no new
  `09_open_questions.md` entry required"); this close raises none. The
  open-question universe is closed at Q-26.

### Part B -- the one Phase-8-carried item, reconciled (Q-26 sub-2)

Phase 8's handoff (`08_findings.md:3097-3106`) records the Q-26 sub-2
NULL-semantics contract as "carried to Phase 9 UNCHANGED ... Phase 8 does not
decide it." Phase 9 reconciles that carry against live source this session
(re-derived, not inherited):

- `app/models/user.py:212-215` (read 2026-05-19): comment reads "Estimated
  effective tax rate during retirement (NULL = unset, fall back to current
  bracket-based estimate)." The bracket-fallback promise is on lines 213-214.
  CONFIRMED -- the documented contract promises a fallback.
- `app/services/retirement_gap_calculator.py:37-136` (full `calculate_gap`
  read): `estimated_tax_rate=None` default (`:43`); `if estimated_tax_rate is
  not None:` guards at `:76` and `:110`; when None, `after_tax_monthly_pension`
  stays None (`:75`), `effective_pension` falls back to gross
  `monthly_pension_income` (`:85`), the after-tax block (`:108-122`) is
  skipped. CONFIRMED -- no bracket-based fallback exists anywhere in the
  function; the code applies no tax adjustment when the rate is unset.
- `app/services/retirement_dashboard_service.py:222-226`: `tax_rate =
  Decimal(str(settings.estimated_retirement_tax_rate)) if settings and
  settings.estimated_retirement_tax_rate else None` -- passes None when unset;
  the `:224` truthiness guard also coerces an explicit `Decimal("0.0000")` to
  None (the F-042-family secondary hazard, as already recorded). CONFIRMED.

Reconciliation verdict: the model-comment-vs-code divergence is REAL, and
A-26 (this file, developer, 2026-05-18) is a COMPLETE adjudication of it.
A-26 explicitly answers the product-contract question -- "should a
bracket-based fallback exist?" -- with NO ("do NOT add a bracket-based
fallback"), with a stated rationale (a silent estimate the user never set is
exactly the hidden-default hazard E-18 and the coding-standard "0 and None
mean different things" exist to prevent). There is NO residual open question.
The Phase-8 "carried UNCHANGED" phrasing is correct only in the narrow sense
that *Phase 8* does not adjudicate questions (the developer does); the matter
was already adjudicated by A-26 before Phase 8 ran. Disposition: Q-26 is
CLOSED in full. Finding LOW-05 (`08_findings.md:2170`) is a settled
documentation-correction finding (correct the comment; do not build the
fallback). The only thing that post-dates the audit is the *remediation*
itself (apply the one-comment fix), which is the separate post-audit exercise
by plan section 11 -- not an open question.

Citation-precision corrections (Phase-9 R-items, recorded per the Q-21
sub-q4 / Q-24 / Phase-8 R-1..R-10 miscite-correction protocol;
documentation-only, no disposition change):

- R-P9-1: the model comment to correct is at `app/models/user.py:212-215`
  (fallback clause on `:213-214`). Q-26 / A-26 / `08_findings.md` LOW-05
  cite it as `user.py:215-216`; live source places the comment at 212-215,
  the CHECK constraint at 216-221, the column def at 242. The LOW-05
  remediation must edit lines 213-214, not 215-216.
- R-P9-2: the second `is not None` guard in `calculate_gap` is at
  `retirement_gap_calculator.py:110`, not `:108` (Q-26 cites `:43,76,85,108`;
  `:108` is the `after_tax_projected = None` initialiser, `:110` is the
  guard). The `:76` guard and the `:85` gross fallback are cited correctly.

### Part C -- audit-level acceptance criteria (`financial_calculation_audit_plan.md` section 11)

| # | Criterion | Verdict | Evidence |
|---|---|---|---|
| C1 | Files 00-09 exist, non-empty, each meeting its deliverable | PASS | 10 files, 890-6204 lines; deliverable attested per phase: P0 priors complete; P1 "Phase 1 remains complete" (P1-f); P2/P3/P4 reconcile-gate + Q-reconcile tails; P5/P6/P7/P8 each G1-G9 9/9; P9 = this section |
| C2 | Every concept in the catalog | PASS | `02_concepts.md:3443` self-certifies 47 controlled-vocabulary concepts (3 prior PRIMARY-PATH-UNKNOWN reconciled); `07_test_gaps.md:3858,3942` independently re-confirms 47/47 (G6) |
| C3 | Every multi-impl concept has a Phase-3 finding with a verdict | PASS | `03_consistency.md` P3-reconcile gate A-E; gate (g) "every UNKNOWN names its blocking Q"; all UNKNOWN-Q axes flipped via E-18..E-28 (commit `da3d108`) |
| C4 | Every reported symptom has a Phase-5 entry + hypothesis tree + root cause | PASS | `05_symptoms.md` G1-G9 9/9; 5/5 symptoms carry every section-3 element; #2/#3/#4 collapse onto the one un-maintained `current_principal` column (E-18) |
| C5 | Findings sorted by severity with file/line evidence | PASS | `08_findings.md` Part 8.A strict order CRIT-01..05, HIGH-01..08, MED-01..07, LOW-01..05 (25 clusters); G5 17/17 evidence + 17/17 severity re-resolved at live source |
| C6 | Open-questions doc contains every uncertainty (Phase-9 scope) | PASS | Part A: Q-01..Q-26, 26/26 parity, zero STILL-OPEN, E-01..E-28 lock; Part B closes the one Phase-8-carried item with no residual |
| C7 | No code/test/migration/template/static modified; only audit-dir files | PASS | 27 commits touched `docs/audits/financial_calculations/`; per-commit `git diff-tree --name-only` shows zero non-`docs/` paths; working tree clean. Only non-subdir docs touched were the Phase-0 bootstrap pair `docs/financial_calculation_audit_plan.md` + `docs/prompt_calculation_consistency_audit_phase_0.md`, both later relocated into the audit dir (commits `830e9739`, `2482e203`, `b87d786`). The plan's literal `docs/audit/` path was deliberately relocated to `docs/audits/`; the criterion is read against the actual location |
| C8 | Every session launched with `--permission-mode plan` | ATTESTED-VIA-PROXY (not artifact-verifiable) | Launch-time permission mode is a process fact not recoverable from git or the filesystem. The mechanical proxy is C7: across all 27 audit commits, zero source/test/migration/template/static mutations -- exactly the property plan-mode enforces. Recorded honestly as not independently re-verifiable rather than rubber-stamped (audit-plan section 10.8 anti-trust-then-verify discipline applied to the audit itself) |

Seven of eight criteria mechanically PASS. C8 is the one
process-attested-not-artifact-verifiable criterion; it is corroborated by
C7's hard evidence and flagged honestly, not asserted as independently proven.

### Part D -- audit completion and handoff

The financial-calculation audit (Phases 0-9) is COMPLETE. Every concept is
catalogued, every multi-path concept has a Phase-3 verdict, every reported
symptom has a Phase-5 root cause, every uncertainty is captured and
developer-answered (Q-01..Q-26 -> E-01..E-28), and the prioritized findings
report (`08_findings.md`, 25 clusters CRIT-01..LOW-05) is severity-sorted with
file/line evidence and surjection-proven against the section-1 source set.

Out of audit scope, the next exercise (audit-plan section 8 final paragraph,
section 11): converting the 25 finding clusters into an ordered remediation
plan. No finding is blocked on an unanswered question -- the three findings
that name a governing Q (HIGH-03/Q-25, MED-03/Q-08, LOW-05/Q-26 sub-2) all
carry a developer A-NN (A-25, A-08, A-26), so the remediation *direction* is
fixed for every cluster; only sequencing and effort remain, and that is the
separate post-audit exercise, not part of this audit.

### Part E -- git status (acceptance criterion 7, this session)

This session's only write is this appended section to
`09_open_questions.md` (a tracked file, so `git status` shows it as modified,
not untracked). No source, test, migration, template, or static file was
touched; all source access this session was read-only (`Read`, `grep`,
`git log`/`diff-tree`, `wc`, `nl`); `pytest` was never invoked; the app was
never run. The verbatim `git status --porcelain` for this session is recorded
beneath the roll-up.

## Phase 9 complete -- financial-calculation audit complete

Acceptance roll-up: **C1 PASS, C2 PASS, C3 PASS, C4 PASS, C5 PASS, C6 PASS,
C7 PASS, C8 ATTESTED-VIA-PROXY -- 7/8 mechanically proven, 1 process-attested
and honestly flagged.** All 26 open questions answered and locked
(E-01..E-28); the one Phase-8-carried item (Q-26 sub-2) reconciled to A-26 at
live source with no residual; two citation-precision R-items recorded for the
LOW-05 remediation. The financial-calculation audit's full deliverable -- ten
phase outputs, developer-adjudicated, trust-but-verify-grounded -- is
complete. Remediation planning is a distinct post-audit exercise and is
explicitly not part of this audit. Session ends.
