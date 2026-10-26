> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Implementation plan: the loan read switch (genesis posting ledger)

Status: COMPLETE -- reconciled as-built through Commit 11 (2026-07-02; C1-C10 committed on `dev`
through `2042569`; C11 shipped as SIX further commits -- see "As-built: Commit 11" -- ending the arc:
reads are ledger-authoritative, the origination anchor write is retired, the readers are fenced. The
whole arc is on `dev`, NOT yet PR'd to `main`.
**Commit 7's runtime code (backfill posts genesis, boundary migration, deploy hook) already landed in
Commit 4** (the unification folded it in); C7 adds only the verification the plan named. Written
2026-07-01 after an
adversarial review of the anchor-based draft
(`~/.claude/plans/i-have-finished-docs-audits-balance-arch-squishy-snowglobe.md`, superseded) and a
firsthand code trace. The developer chose the genesis (opening-equity) design over the anchor-based
intermediate, chose to fix the mortgage-interest-for-taxes surface in this arc, and (2026-07-01) chose
**FULL** -- retire `LoanAnchorEvent` -- over MINIMAL for the read switch (Section 2).

This is the deferred second half of Build-Order Step 4 of Option D
(`level1_level2_scope_and_fitness.md`, build-order item 4: "Post confirmed loan payments with their
real principal / interest split; **retire the read-time replay of confirmed history**"). Step 4
shipped WRITE-ONLY (PR #51); this plan makes the ledger the authoritative source for a loan's
confirmed balance and retires the resolver's confirmed replay.

## As-built through Commit 3 (2026-07-01, on `dev`, not yet PR'd)

Commits 1-3 are done and committed. Two design changes were made during Commit 3; **this section governs
where it disagrees with Sections 3.1-3.3 below** (those are kept for rationale but describe the
pre-Commit-3 design -- the code is the unified design described here):

1. **The walk RESETS at every anchor -- Section 3.2 as-written was WRONG.** Section 3.2's "the
   running-balance walk is unchanged; only the starting point and the eligibility lower bound change" is
   INCOMPLETE and would fail this plan's own penny-exact oracle for every trued-up loan (every fixture +
   the real Mortgage): a from-origination walk that does NOT reset at a user-trueup accrues post-trueup
   interest on the wrong balance. The walk seeds at zero and, in ONE chronological merge of anchors +
   payments, RESETS the running balance to each anchor's verified value as it is reached (a payment due
   exactly on an anchor date sorts BEFORE the reset, so it is subsumed -- the strict
   `anchor_date < monthly_due_date` boundary, pinned by a dedicated test). Proven (unit + code-reviewer's
   independent re-derivation): `-(sum of linked postings)` reproduces the resolver penny-for-penny on a
   trued-up loan (the pre-anchor payment corrections cancel against the anchor correction; the pre-anchor
   payments' interest still lands in the interest ledger).

2. **Opening and true-up are ONE mechanism ("anchor correction").** Sections 3.1 and 3.3 describe them
   separately; they are the SAME operation: each anchor posts `owed_before - anchor_balance` to the
   loan-linked ledger and its negative to the per-loan `equity_opening` account, tagged OPENING for the
   origination anchor (where `owed_before` is 0, giving `-original_principal`) and TRUEUP for a
   user-trueup. `sync_loan_anchor_corrections` reconciles them, keyed by `(source_kind_id, entry_date)`,
   self-healing when a pre-trueup payment moves an earlier anchor's `owed_before`.

**Structure:** the walk change pushed `loan_posting_service.py` (863 lines) over the 1000-line gate, so it
is now a package (mirroring `loan_resolver`):
`loan_posting_service/{_walk,_common,_payments,_anchors,_sync,__init__}.py`. Public API and every caller
unchanged.

**Commits (on `dev`, not yet PR'd):** C1 ref migration `f8a93f1` (docs `2550590`); C2 chart resolver
`3ce8f9b`; C3 opening+true-up posting service `0a0370c` + test-constant dedupe `a7c4a76`. C3's
`sync_loan_anchor_corrections` is built + unit-tested but UNWIRED (that is Commit 4); the payment-walk
change is wired but inert on reads. Full suite 6781; `pylint app/` 10.00; adversarial code-reviewer no
Critical/High.

## As-built: Commit 4 (2026-07-01, green on `dev`, NOT yet committed)

C4 wires the anchor corrections at every go-forward chokepoint. **This governs where it disagrees with
Section 4 item 4 and Section 2 below.** Two developer decisions this session REVERSED the plan-as-written:

1. **KEEP writing `LoanAnchorEvent` (do NOT stop it in C4).** A firsthand trace found Section 4 item 4's
   "stop writing the `LoanAnchorEvent` row here" was self-contradictory: the resolver reads it live
   (`select_latest_anchor`, which raises on empty -- dropping the origination write 500s the loan
   dashboard on a new loan) AND the correction walk (`walk_loan_ledger` -> `load_anchor_events`) DERIVES
   the anchor corrections FROM those rows, so stopping the write starves the very mechanism meant to
   replace it. Keeping the write is what makes C4 additive and inert on reads; the write retirement stays
   at **Commit 11** (as plan item 11 already said). Developer confirmed.

2. **Unify everywhere (folds the "double walk" landmine).** ONE `sync_loan_postings(loan, scenario,
   as_of)` does ONE `walk_loan_ledger`, then reconciles BOTH halves via `reconcile_loan_payment_splits`
   (`_payments`) + `reconcile_loan_anchor_corrections` (`_anchors`), extracted from the old single-half
   syncs. `sync_loan_postings_all_scenarios` iterates `_scenarios_with_loan_payments` UNION {baseline}
   (baseline inclusion posts a payment-less loan's opening at `create_params`). Every prior payment-sync
   caller moved to the unified functions: `create_params` / `update_params`, the transfer
   settle/revert/delete/restore wiring (`_transfer_loan_posting.py`; helpers renamed
   `_sync_loan_postings_if_loan` / `_resync_loan_postings_after_delete`), `sync_all_scenarios_or_duplicate`
   (true-up + rate), and the backfill (renamed `backfill_all_loan_postings`). `loan_owner_id` extracted to
   `_common`. The single-half `sync_loan_payment_postings` / `sync_loan_anchor_corrections` are KEPT as
   isolated test seams.

**Landmine corrections (Section 4 item 4's two C4 landmines were partly wrong):**

- **"Oracle identity (b) breaks" was MISDIAGNOSED.** Identities (b)/(c) `linked == income - per_loan` HOLD
  (anchor corrections are balanced linked+equity pairs, exactly as payment corrections are linked+per-loan
  pairs; re-derived + oracle green). What actually broke was the oracle's `_ledger_balance` helper
  (pre-genesis `anchor - linked_net`) -> fixed to the genesis `-linked_net` (numerically identical on a
  post-anchor-only loan, so `ledger == resolver` still holds). Under genesis `account_posting_total(loan)`
  shifts from "principal" to `-(current balance)`; every updated test literal is `old - V` (V = the
  fixture's anchor balance), verified against actual output; the `+$10` non-vacuity injections still fire.
- **Double walk** folded by the single-walk `sync_loan_postings` (as planned).

**NEW boundary migration `f3d6b1a8c2e4` (code-reviewer HIGH, fixed in C4 -- NOT in the original 11-commit
list).** Booking genesis postings BROKE the head migration's downgrade: C1's ref-seed `d1b22f59ba5b` (the
head) DELETEs the loan_opening/loan_trueup sources + opening/trueup kinds + equity_opening kind, and all
three referencing FKs (`journal_entries.source_kind_id`, `account_postings.posting_kind_id`,
`ledger_accounts.kind_id`) are `ondelete=RESTRICT`, so once any genesis posting exists that downgrade
fails. C1's own docstring anticipated this ("blocks until the higher revisions are downgraded first").
FIX (developer chose add-now over defer-to-C7): new head `f3d6b1a8c2e4` (down_rev d1b22f59ba5b) with a
NO-OP upgrade (genesis is booked at runtime, mirroring Step-4's `e2a9f1c7b4d6`) + a downgrade
`_remove_loan_genesis_postings` (delete loan_opening/loan_trueup entries [legs cascade], then the
equity_opening accounts by kind_id). Verified: Alembic head->base->head; the teardown removes REAL genesis
data (booked through the app) keeping payment/cash/linked; an executed unblock assertion (no posting
carries the opening/trueup kind post-teardown). Both adversarial reviews clean. LESSON: a runtime-booking
commit whose data references RESTRICT ref rows MUST land the higher-revision teardown migration WITH the
booking.

**M2 (code-reviewer MEDIUM, deferred to the read switch -- NOT a C4 defect).** A what-if scenario that
holds the loan but has NO payment IN it nets $0 (no opening -- it is in neither
`_scenarios_with_loan_payments` nor {baseline} unless it IS the baseline). Inert now (reads on the
resolver, account-scoped); the read-switch reader (C8+) must fall back or post per-displayed-scenario
before flipping, or a what-if would show a $0 loan. Multi-scenario is latent (only baseline exists today).

**Gates:** full suite **6785** (baseline 6781 + 4 new tests); `pylint app/ scripts/` 10.00 all
`--fail-on`; migration up/down verified; test template rebuilt to the new head (36 audit triggers
unchanged). Files: the `loan_posting_service` package (`_sync`/`_payments`/`_anchors`/`_common`/`__init__`),
`_transfer_loan_posting.py` + `transfer_service.py`, `routes/loan/params.py`, `scripts/init_database.py`,
the new + touched migrations, and the four loan-posting test files.

## As-built: Commit 5 (2026-07-02, green on `dev`, NOT yet committed)

C5 adds the genesis confirmed-balance READER, still INERT (no consumer reads it; wired at C8/C9,
fenced at C11). Two public functions in a NEW `loan_posting_service/_reader.py` (a READ concern,
distinct from the WRITE modules), re-exported from `__init__`:

- `confirmed_loan_balance_at(loan, scenario, as_of) -> Decimal | None`
- `confirmed_loan_balance_map(loan, scenario, periods) -> OrderedDict[int, Decimal] | None`

Both compute `round_money(-(sum of the loan's LINKED-ledger postings, scenario-scoped, whose
`pay_period.start_date <= as_of`))`. At `as_of = today` this equals
`-posting_service.account_posting_total(loan, scenario)` -- the C4-oracle-proven quantity; the
pay-period-start bound generalises it to any historical date, and the map applies it at every period
boundary. Because postings are period-ASSIGNED and periods contiguous, `<= period.start` and
`<= period.end` select the identical set, so the map (keyed by start) IS the canonical period-END-keyed
`account_projection.compute_loan_period_balance_map`.

**One decision the plan left open ("clamp or raise"), resolved by the developer this session
(AskUserQuestion): SCALAR RAISES, MAP CARRIES FLAT.** `confirmed_loan_balance_at` raises `ValueError`
on `as_of > today` (a future date is a projection; route to `resolve_loan`) -- it is only ever called
at today, so a future date is a caller bug. `confirmed_loan_balance_map` does NOT raise: a future
period has no confirmed postings, so it carries the last confirmed balance flat, letting the C9 caller
pass its whole display window and overlay the projection on the future tail. Principled asymmetry: a
single future point is ambiguous intent (raise); a future period has a well-defined confirmed value
(carry flat).

**`None` sentinel** = no OPENING-kind leg on the linked ledger in the scenario (`_has_opening_posting`,
scoped to the linked ledger so the same-kind equity leg is not matched; scoped to the scenario so the
M2 what-if-with-no-payment reads `None`, not the baseline's balance). A configured loan that is paid
off returns `Decimal("0.00")`, distinct from `None`. Owed is `round_money(_ZERO_MONEY - net)` (not
`-net`) so a zero net (a configured loan read before its opening period, or a paid-off loan) yields a
clean `0.00`, never `-0.00`.

**DRY/correctness:** a shared `_scope_to_linked_ledger(query, linked_id, scenario_id)` is the FROM /
JOIN / WHERE both readers use (they differ only in projection + tail), so they cannot drift on which
postings they sum -- `map[P] == confirmed_loan_balance_at(P.start_date)` by construction.

**Tests (12):** `TestConfirmedLoanBalanceReader` (11: opening-only, on-schedule single, extra-payment
captured with no true-up [the arc's headline], running-balance = resolver 98492.49, paid-off = 0.00
not None, payoff overpayment = 0.00 with the excess on the Refund ledger, `as_of` bound at period
boundaries [incl. start==end within a period], unconfigured -> None, future-`as_of` raises + in-domain
still reads, scenario isolation + per-scenario None, per-period map running + carry-flat) +
`TestConfirmedLoanBalanceReaderFuturePeriods` (1: map carries future periods flat while the scalar
raises). Hand-computed literals; class-scoped `freeze_today` (does NOT leak into the split/anchor tests
above). Non-vacuity mutation-verified two ways: removing the `<= as_of` bound fails the bound test;
`bisect_right -> bisect_left` fails both map tests; `is_signed()` locks the clean-zero.

**Gates:** full suite **6797** (baseline 6795 + 2 [10 reader tests added across the session]); `pylint
app/ scripts/` 10.00 all `--fail-on`; adversarial `code-reviewer` no Critical/High (it re-ran its own
mutation tests). NO migration (pure reader) -> no template rebuild. The test file's only pylint
findings (C0302 / C1803 / unused `db`/`seed_periods`) are PRE-EXISTING from C3/C4 (HEAD lints 9.82;
tests are not CI-gated) -- C5 adds zero new findings.

**Carry-forward for C8/C9 (code-reviewer LOW, NOT a C5 defect):** the reader trusts its
`loan_account_id` / `scenario_id` args, matching the sibling `account_posting_total` service
convention. The wiring commits MUST scope `loan_account_id` to the authenticated user via the existing
ownership helpers and 404 on not-found/not-yours BEFORE calling the reader, since C8 is the first path
that turns a request parameter into a reader call.

Files: `loan_posting_service/_reader.py` (new), `loan_posting_service/__init__.py`,
`tests/test_services/test_loan_posting_service.py`.

## As-built: Commit 6 (2026-07-02, green on `dev`, NOT yet committed)

C6 is the oracle gate before any read switch. It extends the loan reconciliation oracle
(`test_posting_ledger_loan_reconciliation.py`) with a new `TestReaderParallelRunAgainstResolver` class
(7 tests) that parallel-runs the C5 READER (`confirmed_loan_balance_at` scalar +
`confirmed_loan_balance_map`) against the resolver as a THIRD independent producer. This is distinct
from Sections 1-6 (which parallel-run the test's OWN independent `-(sum of linked postings)` query) and
from the C5 UNIT tests (which pin the reader against hand-computed literals): C6 pins the exact function
the read switch (C8/C9) wires against an independent producer that shares none of its code path. **Test
only; no app code changed.**

**The equivalence the gate rests on (verified by a firsthand trace + the code-reviewer's independent
re-derivation, not assumed).** Both the reader and the resolver cap by PAY-PERIOD START: the reader sums
linked postings whose `pay_period.start_date <= as_of` (`_reader.py:193`), and
`rate_period_engine.replay_schedule`'s as-of cap is `period_start <= as_of`
(`is_confirmed_payment_eligible`, `:319`; docstring `:696` "the as_of cap uses the pay-period start").
So `confirmed_loan_balance_map(periods)[P] == resolve_account_loan(loan, scenario, P.start_date)
.current_balance` for EVERY period, on-schedule; the map test asserts it across the whole 10-period
`seed_periods` window (stepping-down region periods 0-3 + carried-flat tail periods 4-9, since
`current_balance` counts only confirmed payments and so also carries flat). The equivalence is exact
because every anchor precedes the read window: the SPLIT_LOAN anchor (2026-01-10) and the origination
opening (clamped to the earliest period via `_anchors._resolve_anchor_pay_period`) BOTH land in period 0
(2026-01-02..01-15), so opening + true-up are counted in every period and no period shows a pre-true-up
balance. The map across a MID-LIFE true-up (reader keeps pre-true-up history, resolver reseeds -- a
deliberate divergence) is C9's concern, noted in the map test docstring.

**Seven tests, one per plan-4-commit-6 / 7.2 case:** scalar on-schedule (`reader == resolver ==`
independent ledger query); per-period map across the window (+ non-vacuity steps-down + carry-flat);
off-schedule diverges by EXACTLY the principal delta (extra `resolver - reader == 2000 - monthly_pi`;
short `reader - resolver == monthly_pi - 1000`); the pre-true-up payment summed (`== 100000` anchor, no
pollution -- split pinned DIRECTLY via `compute_loan_payment_splits[0].interest == 1250.00` because the
reader value there is split-invariant); a mid-life true-up through the real `apply_loan_anchor_true_up`
chokepoint (reader jumps 99500 -> 95000 == resolver); a calendar-year boundary on a `bare_user` with a
period straddling 2025-12-31 (reader at 2025-12-31 counts the straddling period's payment by its START,
excludes the January one -- both due-months distinct, no biweekly-collision shift -- == resolver);
scenario isolation (baseline 1 payment / what-if 2, each `== its own resolver`, neither leaks) + the
unconfigured -> `None` route.

**Non-vacuity re-proven the gold-standard way.** Re-running the `+$10` interest-bug injection at
`_walk.py:200` (NOT `money.py`, which the resolver shares in lockstep) failed ALL 7 new tests -- every
value assertion, via the independent resolver / hand-computed literals. (The pre-true-up test's reader
VALUE is split-invariant -- a wrong split cancels against the true-up's `owed_before` on the one walk --
so its split is pinned DIRECTLY on `compute_loan_payment_splits`, a code-reviewer MEDIUM fixed here so
the "fails every value test" claim is true and the test is not vacuous; its Step-4 sibling
`test_pre_anchor_payment_is_correctly_summed_under_genesis` pins the same split the same way.)

**Code-reviewer:** no Critical/High (it re-derived every equivalence from source and recomputed every
literal). 1 MEDIUM (pre-true-up test's non-vacuity claim was false -- fixed by restoring the direct split
pin) + 3 LOW (a "pre-origination" misnomer -> renamed `test_reader_includes_the_pre_trueup_payment`; the
map docstring's "EVERY period" generality qualified to the anchor-in-period-0 fixture; `too-many-lines`
on the test module is not a gate -- `tests/` is outside the design-smell pylint scope) -- all addressed.

**Gates:** full suite **6804** (baseline 6797 + 7); the test file adds ZERO new pylint findings (the 6
pre-existing `_make_loan` / `test_two_owners` / migration-teardown R09xx/W0212 findings unchanged; score
9.85). NO migration (test-only) -> no template rebuild. Files: `test_posting_ledger_loan_reconciliation.py`
(module docstring invariant 7; `_reader_balance` / `_reader_period_map` / `_seed_boundary_loan` helpers;
the new class).

## As-built: Commit 7 (2026-07-02, green + verified on `dev`, NOT yet committed)

**C7's RUNTIME CODE already shipped in Commit 4 -- a firsthand trace + a green test run confirmed it.**
The plan (written pre-C4) assumed C4 would wire only go-forward postings, leaving C7 to extend the
backfill to genesis. But C4's unification did both at once: `backfill_all_loan_postings` was rewired onto
`sync_loan_postings_all_scenarios` (ONE `walk_loan_ledger` per (loan, scenario), which loads ALL anchors,
resets at each, and emits a correction per anchor + a split per payment), so it ALREADY posts the opening +
every payment correction + every true-up for every loan across every scenario. The boundary migration
`f3d6b1a8c2e4` (current head) and the deploy hook `backfill_loan_payment_postings_after_migration`
(`scripts/init_database.py`, docstring already updated to "opening/true-up/splits") also landed in C4. The
genesis backfill tests landed in C4 too (`test_loan_posting_backfill.py` +236; the oracle's
`TestBackfillEqualsGoForward` +178). So C7 is NOT new backfill code -- it is the two verifications the plan's
C7 line names ("backfill == go-forward" was already covered; "the oracle detects the unposted-opening gap
before and zero mismatches after" and "verify up/down on the prod-clone dev DB" were not).

**1. The reader gap-test (the one real code gap; the oracle, not the backfill suite).** No test ran the C5
reader -- `confirmed_loan_balance_at` / `confirmed_loan_balance_map`, the exact functions C8/C9 wire --
around the backfill (verified: neither the backfill suite nor `TestBackfillEqualsGoForward` called it). New
`TestBackfillEqualsGoForward::test_reader_reads_none_before_backfill_then_matches_resolver_after`: an
on-schedule payment, then clear all genesis postings (the two boundary teardowns) -> BOTH reader producers
return `None` (the unposted-opening GAP; a read switch flipped HERE would show needs-setup) while the
resolver -- which never reads the ledger -- is unchanged; then backfill -> the scalar AND every period of the
map read back == the resolver to the penny. Closes the transitivity gap (backfill == go-forward and reader ==
resolver-on-go-forward were each proven, but reader == resolver on a BACKFILLED loan was not asserted
directly). Non-vacuous the gold-standard way: the `+$10` injection at `_walk.py:200` (NOT `money.py`, shared
with the resolver) makes the reader `99011.12` vs the resolver `99001.12` -- fails the test. Class docstring
generalized to cover both faces (ledger equivalence + reader authority).

**2. The prod-clone executable up/down verification (the plan's C7 manual step) -- DONE, all clean.** Run on
an ISOLATED clone (`shekel_c7verify`, `pg_dump` of the live prod-clone dev DB into a throwaway database,
dropped after) so the live `shekel-dev-app` (Up 47h) was never disrupted; the live dev DB was confirmed
untouched (still `e2a9f1c7b4d6`, 0 genesis). The clone carried TWO real loans -- the Mortgage (orig $202,000
2018-12, one true-up to $177,829.83, WITH pre-anchor payments) and the Van Loan (orig $32,402.45 2023-02,
TWO true-ups). Four stages:

- **Baseline** (`e2a9f1c7b4d6`, pre-read-switch): trial balance 0.00; reader `None` for both loans (the gap);
  resolver Mortgage 177,554.69 / Van 15,663.59.
- **Upgrade + deploy-hook backfill** (`e2a9f1c7b4d6` -> `d1b22f59ba5b` -> `f3d6b1a8c2e4` via the real
  `init_database.py`): clean; genesis posted (opening 2, trueup 3, payment 6); trial balance 0.00; and
  **the genesis reader == the resolver to the PENNY on BOTH real loans** (Mortgage 177,554.69, Van
  15,663.59). This directly resolves the Step-4 "$3,821.90 naive-read-switch mis-statement": genesis
  reproduces the resolver on the real Mortgage WITH pre-anchor payments, because the true-up correction
  absorbs the pre-anchor history (the 275.14 below the true-up is the one on-schedule post-true-up payment).
  (The reader == resolver equality held here because every post-true-up payment was on-schedule; the read
  switch's value is the OFF-schedule case, which the synthetic oracle covers -- the real-data run proves the
  reconciliation, not that real payments are always on-schedule.)
- **Downgrade** (`f3d6b1a8c2e4` -> `d1b22f59ba5b` -> `e2a9f1c7b4d6`): the RESTRICT-unblock the boundary
  migration exists for, proven WITH real data -- the genesis teardown cleared the opening/true-up postings +
  equity accounts BEFORE the ref-seed deleted the `opening`/`trueup`/`equity_opening` ref rows under their
  RESTRICT FKs, so it did NOT jam. Genesis gone, the 6 Step-4 payment corrections survive, trial balance 0.00.
- **Re-upgrade + backfill**: identical re-post to the upgrade stage (reader == resolver == same values);
  a second backfill posts NOTHING new (114 -> 114 entries) -- idempotent, reversible.

**Scope boundary (C7 vs C8).** The plan's Section-7 item-6 manual step also lists "mark the Mortgage's next
payment Paid -> the balance drops on the loan card / savings tile / net worth." That is a READ-SWITCH (C8+)
behavior -- reads still flow through the resolver until C8, so it is NOT verifiable at C7 and is deferred to
the flip. C7 verified the C7-scoped half: the ledger sums to the displayed balance on real data, and the
migration round-trip.

**Gates:** full suite (run alone) shown at commit; `pylint app/` 10.00 all `--fail-on` (app/ unchanged by C7 --
the `+$10` injection was reverted with zero diff); NO migration (test + docs only) -> no template rebuild. The
oracle test file's only pylint findings are the pre-existing accepted set (C0302 module size, R09xx on
`_make_loan` / `test_two_owners`, migration-teardown W0212) -- C7 adds two more W0212 of the SAME sanctioned
teardown pattern; `tests/` is outside the design-smell scope and not CI-gated. Files:
`test_posting_ledger_loan_reconciliation.py` (the new reader test + generalized class docstring),
`test_loan_posting_backfill.py` (two docstrings updated: the manual step is now DONE), this plan, and the
memory.

## As-built: Commit 8 (2026-07-02, green on `dev`, NOT yet committed)

C8 is **the flip** -- the FIRST commit where a loan's displayed current balance reads from the genesis
ledger instead of the resolver's anchor replay. **This governs where it disagrees with Section 4 item 8
and Section 3.5 below.**

**The seam.** A new optional `forward_seed_balance: Decimal | None = None` on `resolve_loan`,
`compute_payoff_scenarios`, `target_date_outlook`, and `_build_forward_inputs`. When supplied it overrides
BOTH the headline `current_balance` AND the forward projection's `starting_balance` (Section 3.5's "one
value, threaded once" -- the docstring warns against the desync the prior draft's two-mechanism override
risked). Only the balance is overridden: `next_pay_date` / `remaining_months` come from the confirmed-payment
COUNT and DATES, identical seeded or not, so the projection amortizes the real owed balance over the same
remaining term. `None` leaves the resolver on its anchor replay, byte-identical to pre-C8 (the existing 42
resolver tests pass unchanged).

**The one injection helper.** `loan_payment_service.confirmed_loan_seed(account_id, scenario_id, as_of)` is
the SINGLE call site of the genesis reader (`confirmed_loan_balance_at`) -- the seam C11 allowlists for the
W9906 fence. It returns `None` (-> resolver anchor-replay fallback) when the ledger cannot answer:
`scenario_id is None`, `as_of > today` (a projection, guarded BEFORE the reader so its raise is never hit),
or the reader returns `None` (no OPENING posting: unconfigured / M2 what-if / un-backfilled). The ONE
non-fallback path is a loan with no linked ledger at all (`PostingError`, fail-loud -- unreachable for a
configured loan). `resolve_loan_seeded(loan_inputs, account_id, scenario_id, as_of)` is the shared "read
seed + resolve" the three loaders route through.

**Wired: the three loaders PLUS the on-page forward projections (developer chose the fuller scope over the
plan-literal three).** A firsthand trace found the plan's "three loaders" leaves the loan-detail chart /
schedule tab and the payoff / target-date calculators on the anchor replay -- they call
`compute_payoff_scenarios` / `target_date_outlook` DIRECTLY, bypassing `resolve_loan` -- so off-schedule the
card (ledger) would visibly disagree with the chart on the SAME page (exactly the desync Section 3.5 warns
of). Developer chose (AskUserQuestion) to seed those too: `resolve_account_loan` / `_helpers._resolve` /
`_projections._compute_loan_account` via `resolve_loan_seeded`; `dashboard._build_dashboard_scenarios`
(refactored to take `loan_inputs` + `scenario_id`, floor via `dataclasses.replace`) and `calculators.py`
read the seed via `confirmed_loan_seed`. `_loan_ever_paid_off` (`date.max`) and `_resolve_loan_piti`
(balance-independent PITI) stay UN-seeded, correctly.

**Landmine C8 surfaced (NOT in the plan): the flip breaks the reconciliation oracle's independence.**
`test_posting_ledger_loan_reconciliation.py`'s `_resolver_balance` used `resolve_account_loan` as the
"independent producer that never reads the ledger" (its load-bearing invariant) -- but C8 makes
`resolve_account_loan` read the ledger, so that producer would collapse to a tautology. FIX: `_resolver_balance`
now builds the SAME `LoanInputs` and calls `resolve_loan` UN-seeded (the exact pre-flip producer), restoring
independence with every divergence/agreement assertion unchanged. NEW `TestReadSwitchProductionPath` (2 tests)
pins the flip itself: the SEEDED production path == ledger == reader off-schedule and DIVERGES from the
un-seeded replay by exactly the extra/short principal (non-vacuous). LESSON: a read switch that flips a
producer the oracle used as its independent reference must re-point the oracle at the un-seeded producer, or
the oracle silently goes vacuous.

**NEW R0401 (not in the plan).** `confirmed_loan_seed`'s import of `loan_posting_service` closed a static
cycle (`loan_posting_service/_sync.py` imports `loan_payment_service` at module top). FIX: import
`confirmed_loan_balance_at` from its defining `_reader` submodule (which imports nothing back), so the static
graph carries no cycle; lazy + runtime-safe.

**M1 (code-reviewer MEDIUM, accepted staging -- NOT a C8 defect).** C8 flips the SCALAR balances but leaves
the confirmed HISTORY rows (amortization table, C11) and the per-period MAPS (net-worth trend / year-end, C9)
on the replay, so OFF-schedule the card disagrees with its own schedule table and the trend until C9 + C11
land. Intentional (the plan stages scalar/map/history separately); prod is entirely on-schedule today (C7),
so ledger == replay and the window is inert until a real off-schedule payment. The whole arc PRs to prod
together, closing the window. The intra-page card-vs-table discontinuity is unasserted (pinning a transient
artifact resolved in C11 is not worth a test).

**Gates:** full suite **6815** (baseline 6805 + 10 new tests: `TestForwardSeedBalance` 3, `TestReadSwitchSeedHelpers`
4, `TestReadSwitchProductionPath` 2, off-schedule cross-page 1); `pylint app/` 10.00 all `--fail-on`; adversarial
`code-reviewer` NO Critical/High (it re-derived every literal + the oracle equivalences). NO migration -> no
template rebuild. Files: the resolver package (`_state`/`_payoff`), `loan_payment_service.py`,
`routes/loan/{_helpers,dashboard,calculators}.py`, `savings_dashboard_service/_projections.py`, and the four
touched test files + `conftest.py` (the off-schedule fixture).

## As-built: Commit 9 (2026-07-02, green on `dev`, NOT yet committed)

C9 is **the per-period MAP read switch** -- the net-worth surfaces that read a loan's
per-period balance map (the /savings net-worth trend and the year-end net-worth Section 5)
now read the genesis confirmed ledger for every period that has BEGUN by today, keeping the
C8-reseeded schedule projection for the future. This closes M1 for the map surfaces (the C8
cross-page test's deferred half). **This governs where it disagrees with Section 4 item 9 and
Section 3.6 below.**

**SCOPE REFINEMENT (developer-ratified this session): the SCALAR is DEFERRED to C11 -- C9 is
the MAP only.** The plan's commit-9 line ("map + past scalar") is REVERSED for the scalar half.
A firsthand trace found the ONLY consumer of the date-precise loan scalar
(`balance_at.balance_at`) is the year-end **debt-progress** section, which reads `balance_at(Dec
31 of the target year)` -- a date that is routinely OUTSIDE the user's pay-period window (verified:
`seed_periods` is 10 biweekly periods spanning Jan-May 2026, yet the debt-progress test reads Dec
31 2025 and Dec 31 2026). The current date-precise scalar answers those out-of-window dates by
walking the UNBOUNDED amortization schedule; the per-period map is BOUNDED to the window (an
out-of-window date falls through to `resolve_anchor`). So "route the scalar through the map"
cannot answer the year-end dates. The scalar's off-schedule correctness resolves CLEANLY at C11:
once the schedule's history rows become ledger-derived, the same date-precise walk is
ledger-correct -- unbounded, symmetric (no boundary off-by-one), attribution unchanged, the
date-precision invariant preserved. So **`balance_at.py` is intentionally unchanged in C9.**
(Correction to this session's earlier framing: it over-stated the cost as "re-derive every
debt-progress literal" + "reverse the date-precision invariant"; both are wrong -- every existing
debt-progress / net-worth test loan is NON-genesis, so it hits the schedule-only fallback and its
hand-computed literals are byte-identical, and the scalar is untouched.)

**The mechanism (two new functions, one behavior).**

- `account_projection.splice_confirmed_and_projected_loan_balances(periods, confirmed_map,
  projected_map, as_of)` -- PURE, fence-cluster: for each period, `start_date <= as_of` (begun)
  reads `confirmed_map`, else `projected_map`. The boundary rule lives in one tested place.
- `net_worth_kernel._build_amortizing_balance_map(account, scenario, periods, debt_schedule)` --
  the AMORTIZING branch of `build_account_balance_map`. Builds the schedule map
  (`compute_loan_period_balance_map`, whose `current_balance` fallback is the F-21 pre-first-payment
  value), reads the confirmed ledger map (`confirmed_loan_balance_map`), and splices. `confirmed_map
  is None` (no OPENING posting: unconfigured / un-backfilled / a what-if never posted into) returns
  the schedule-only map, **byte-identical to pre-C9** (safe by construction).

**NO loan_payment_service funnel (differs from the C8 scalar's `confirmed_loan_seed` pattern).** The
map reader is called DIRECTLY from `net_worth_kernel` (a lazy import of `loan_posting_service._reader`,
keeping the static graph acyclic). Two reasons this is cleaner than a `loan_payment_service` funnel:
(1) `net_worth_kernel` is already in `_BALANCE_SEAM_MODULES`, so the call is fence-clean now and when
the reader joins the W9906 fence at C11 -- no new module to allowlist; (2) adding the funnel pushed
`loan_payment_service` over the 1000-line gate (a package split is out of C9's scope). `net_worth_kernel`
crossing 1000 lines was resolved by tightening the new helper's docstring, NOT a package split.

**The load-bearing equivalence (verified by the code-reviewer's independent re-derivation).**
`confirmed_loan_balance_map[current_period] == confirmed_loan_balance_at(today) ==
debt_schedule.current_balance` (the C8 seed): both sum the linked postings whose pay period started
by today, and no pay period starts in `(current_period.start_date, today]` (periods are contiguous
and the current one contains today), so the sets are identical. The `<=` boundary is therefore what
makes the trend/year-end map at today AGREE with the /savings tile and loan-detail scalar (a `<`
would desync them). Locked by the cross-page test (map-at-today == scalar == ledger) and the
inclusive-boundary unit test.

**Intended behavior change (an improvement, GENESIS loans only).** For a genesis loan, pre-payment /
pre-anchor begun periods now show the REAL historical trajectory (declining from the origination /
anchor balance; `0.00` before origination) instead of the old flat-current-balance-carried-backward.
Post-payment begun periods are unchanged on-schedule (`confirmed == resolver`, the C6 oracle). Prod
is entirely on-schedule (C7), so the CURRENT balance and recent history are unchanged; only a genesis
loan whose origination/first-payment sits INSIDE the visible window shows the improved back-history.

**Tests.** The cross-page oracle's `test_scalar_surfaces_read_the_ledger_off_schedule` was broadened
to `test_all_surfaces_read_the_ledger_off_schedule`: off-schedule, ALL FOUR loan surfaces (the two C8
scalars + the two C9 maps -- year-end net-worth + net-worth trend) read the ledger, closing the C8
deferral. New `TestAmortizingReadSwitch` (2): a PURE splice test pinning the inclusive begun boundary
(a period with `start == as_of` reads confirmed), and an end-to-end `build_account_balance_map` test
on the off-schedule genesis fixture asserting `dense[begun] == confirmed` and `dense[future] ==
projected` with explicit `begun_divergence` + `future_divergence` guards so neither half passes
vacuously. **Non-vacuity proven the gold-standard way:** reverting the app splice to HEAD makes the
broadened cross-page test FAIL (the map surfaces read the replay); restoring it passes.

**Code-reviewer:** NO Critical/High (it re-derived the `<=`-boundary equivalence from source,
confirmed no KeyError -- both maps key every period in the one shared list -- confirmed C9 adds NO new
`PostingError` surface [C8's `resolve_account_loan -> confirmed_loan_seed -> _ledger_account_for`
already runs on the same non-None scenario at `today` when producing the `debt_schedule`], and
confirmed the W9906 fence is clean). 1 LOW fixed (a docstring xref `_amortizing_balance_map` ->
`_build_amortizing_balance_map`). 2 LOW accepted: (L2) the map-vs-scalar interim inconsistency IS the
ratified C11 deferral -- recommend sequencing C11 promptly; (L3) `date.today()` is evaluated
independently by `generate_debt_schedules` and the splice (a midnight-rollover microsecond race),
negligible and consistent with the existing `generate_debt_schedules` pattern -- if ever wanted
deterministic, carry `as_of` on the `DebtSchedule` bundle.

**Gates:** full suite **6817** (baseline 6815 + 2 new `TestAmortizingReadSwitch`; the cross-page test
was broadened in place, net 0). `pylint app/ scripts/` 10.00 all `--fail-on` (incl. W9906). NO
migration (pure reader consumer) -> no template rebuild. Files: `account_projection.py` (the splice),
`net_worth_kernel.py` (`_build_amortizing_balance_map` + the AMORTIZING branch),
`test_net_worth_kernel.py`, `test_cross_page_balance_equality.py`, and `conftest.py` (the off-schedule
fixture docstring). `balance_at.py` UNCHANGED (scalar -> C11).

## As-built: Commit 10 (2026-07-02, green on `dev`, NOT yet committed)

C10 is the **tax-interest hybrid**. `_compute_mortgage_interest` now returns, per loan
(`_loan_year_interest`), the ledger-ACTUAL interest of confirmed payments PLUS the schedule-PROJECTED
interest of the year's not-yet-confirmed rows -- replacing the old "sum every schedule row by
`payment_date.year`", which used the schedule's REPLAYED interest even for off-schedule payments (the
replay advances by the contractual `period_pi`, so it never reflects an extra/short payment's real
split; `rate_period_engine.py:598`). A loan with no genesis opening posting falls back to the full
schedule -- byte-identical to pre-C10 (the existing non-genesis mortgage tests pass unchanged).

**DESIGN CHANGE vs Section 3.6 (developer-ratified this session via AskUserQuestion + a worked
example): attribute ledger interest by each payment's CURRENT civil paid date, NOT by the posting's
`entry_date`.** Section 3.6's `Sigma(ledger INTEREST postings with entry_date in year)` is WRONG across
a year boundary: reverting a payment CLEARS `paid_at` (`transfer_service.py:478`), so its reversal leg
is dated at `pay_period.start_date` (the `_civil_settle_date` NULL fallback), which can land in a
DIFFERENT year than the original interest leg -- stranding a spurious +/- interest and mis-stating BOTH
Schedule A years. The new reader `confirmed_loan_interest_in_year` GROUPS the loan's `loan_interest`
legs by their payment shadow (`journal_entries.transaction_id`) and attributes each payment's NET
interest to `_civil_settle_date(shadow.paid_at, shadow.pay_period)`, so a reverted payment (net zero)
drops out of every year cleanly, and a re-settle / edited-paid-date reports its net at its current paid
date. For on-schedule payments this equals the entry-date sum, so no common-case number moves. Only
`LOAN_INTEREST`-kind legs are summed (escrow / refund excluded); `None` when no OPENING posting.

**Key traced facts.** (1) `LOAN_INTEREST` legs are written ONLY by `_reconcile_loan_payment`
(`_payments.py:254`, `transaction_id=shadow.id`); a HARD-deleted payment's legs are `ON DELETE SET
NULL` after being reversed-to-zero pre-delete, so the reader's explicit `transaction_id IS NOT NULL`
filter (code-reviewer L1) drops that dead group by intent, not incidentally. (2) The resolver projects
from the LAST CONFIRMED payment forward (NOT from today), so a year always carries projected rows
unless fully settled -- "past year all ledger" is the projected=0 special case (the paid-in-2025
integration test, whose loan has no 2025 schedule rows). (3) The confirmed/projected partition is exact
and non-overlapping: a settled payment is only in the ledger (its schedule row is `is_confirmed=True`,
excluded from the projected sum, or absent when pre-anchor); a non-settled payment is only in
schedule-projected (no ledger leg).

**Tests.** `TestConfirmedLoanInterestReader` (8 unit: on-schedule, running sum, paid-date attribution,
cross-year revert nets to zero [the headline -- non-vacuity PROVEN by swapping the reader to a naive
by-entry-date sum, which fails ONLY this test], hard-deleted payment not deducted, None sentinel,
scenario isolation, escrow excluded) + `TestMortgageInterestGenesisHybrid` (2 integration: current-year
ledger+projected with an explicit new!=old divergence; paid-in-2025 all-ledger year). Existing
`TestIncomeAndTax` mortgage cases (non-genesis) unchanged.

**Adversarial `code-reviewer`: NO Critical/High/Medium** (it re-derived the partition completeness, the
IDOR-clean caller chain, the sign, and the non-vacuity from source). 2 LOW, both fixed: L1 (the
"never NULL" comment was wrong -- `transaction_id` is SET NULL on hard delete -> added the explicit
`isnot(None)` filter + accurate comment + the hard-delete test); L2 (a tautological test assertion ->
removed).

**Gates:** full suite **6827** (baseline 6817 + 10). `pylint app/ scripts/` 10.00 all `--fail-on`. NO
migration -> no template rebuild. Files: `loan_posting_service/_reader.py`
(`confirmed_loan_interest_in_year`), `loan_posting_service/__init__.py` (re-export + `._reader`
docstring), `year_end_summary_service/_income_tax.py` (`_compute_mortgage_interest` +
`_loan_year_interest`, `scenario_id` threaded), `_orchestrator.py` (passes `scenario.id`), and the
three test files (`test_loan_posting_service.py`, `test_year_end_summary_service.py`,
`test_loan_unified_figures.py` [the one direct-call signature caller]).

## As-built: Commit 11 (2026-07-02, on `dev`; shipped as SIX commits)

C11 is the arc's final commit: the resolver's read-time replay of confirmed history is retired from
every surface, the amortization table is ledger-derived, the origination anchor write is retired, and
the genesis readers join the W9906 fence. **This governs where it disagrees with Section 4 item 11 and
Section 2 below.** Shipped as six independently-green commits (each adversarially reviewed):

1. **`loan_loaders` leaf extraction (prep, `2397764`).** The pure row loaders + `query_shadow_income`
   moved from `loan_payment_service` into a new leaf `app/services/loan_loaders.py` (+
   `load_rate_history`, deduplicating the raw rate query). NECESSARY, not cosmetic: pylint's R0401
   counts function-local imports, so the C11a reader's need for the loaders inside
   `loan_posting_service._reader` -- while `loan_payment_service` lazily imports that same `_reader` --
   was a true structural cycle no lazy import could break. The leaf breaks it by layering.
2. **C11a -- the ledger-derived history reader (`e6ca220`).** `confirmed_loan_history_rows` in
   `_reader`: one `AmortizationRow` per confirmed payment from POSTED legs (interest = the
   `loan_interest` legs per shadow, shared with the C10 tax reader via `_interest_net_by_shadow`;
   principal = the payment's linked net; running balance = cumulative linked net with anchors/other
   events at their entry dates in the write walk's order). Row algebra matches forward rows:
   `principal + interest == payment + extra_payment`, extra vs the governing `period_pi`, so the
   schedule totals need no special-casing. **Adversarial-review H2 (fixed pre-commit): classification
   is source-kind + payment-lineage aware** -- non-confirmed payment lineage (reverted / cancelled /
   soft-deleted residue at TWO entry dates; early-settled future-period payments) is DROPPED exactly,
   while transfer-outs / raw settled transactions stay honest dated events. On-schedule rows are
   byte-identical to the replay's (pinned field-by-field against `resolve_loan`), EXCEPT biweekly
   due-month collisions (two same-month rows at true due dates -- more truthful; balances agree).
3. **C11b -- the history/read flip (`62151a8`).** The C8 scalar `forward_seed_balance` is replaced by
   ONE bundled `ConfirmedLedgerView(balance, history_rows)` threaded once through `resolve_loan` /
   `compute_payoff_scenarios` / `target_date_outlook`; `confirmed_loan_seed` becomes
   `confirmed_loan_view` (all-or-nothing: either half `None` -> whole view `None`, so no path can pair
   a ledger balance with replay rows). Flips: the amortization table + chart history prefix (actual
   economics, pre-true-up payments the replay hid now shown), the `balance_at` DATE-PRECISE scalar
   (the C9-deferred half -- exact through the last confirmed payment; beyond it the walk keeps its
   pre-existing DUE-BASIS projection attribution, so at today-with-an-overdue-payment it deliberately
   reads below the card: a scheduled-but-unmade payment is counted as made only on the projection
   side), and the C10 tax hybrid's confirmed region now AGREES with the displayed schedule. Closes C8
   M1 in full (cross-page oracle: all SIX loan surfaces == ledger off-schedule). Replay stays as the
   projection's starting-date/remaining-months producer and the None-view fallback (M2 what-if /
   fresh user / un-backfilled), byte-identical.
4. **Refinance forward-only baseline (`5e0c26e`, review H1).** `_build_refinance_comparison` compared
   a from-today refinance against WHOLE-schedule aggregates (`len(state.schedule)`,
   `state.total_interest`) -- pre-existing skew that C11b widened to the full recorded history on
   trued-up loans. Root fix: the current side reads the FORWARD (non-confirmed) slice only; the
   hand-pinned all-forward literals ($255,085.82 / $68,572.58) are byte-identical.
5. **C11c -- retire the origination anchor write (`d723273`).** **AMENDS the FULL decision (Section
   2): the `user_trueup` write is KEPT.** A true-up is the operator's dated balance assertion -- the
   SOURCE DOCUMENT the self-healing TRUEUP reconcile derives `owed_before - V` from and re-derives
   against when pre-true-up history changes; without stored `V`, a later payment edit silently
   violates the asserted balance (worked example in the C11c commit message), and any replacement
   store is `LoanAnchorEvent` renamed. The ORIGINATION write IS retired: it was a verbatim copy of
   the immutable `LoanParams` (verified on real prod data), so `create_params` no longer writes it and
   ALL consumers synthesize it via the new `loan_loaders.LoanAnchorFact` +
   `load_loan_anchor_facts(params)` -- the ONE anchor loader the genesis walk AND every
   resolver-input builder share (the walk keys OPENING/TRUEUP on `fact.is_opening`; the synthesized
   origination carries the earliest UTC instant so same-day true-ups still win the latest-anchor
   tie). Legacy origination rows: kept as append-only history, ignored by every consumer, no
   migration. Dead weight removed: `load_anchor_events`, the route-local anchor loader,
   `_resolve_loan_piti`'s no-anchor guard, and `compute_contractual_pi` /
   `compute_monthly_payment_baseline`'s unused anchor/payments compat params.
6. **C11d -- the W9906 fence + docs (this commit).** `confirmed_loan_balance_at` /
   `confirmed_loan_balance_map` join the fence with a reader-specific allowlist: the seam cluster
   PLUS the defining `loan_posting_service` package and `loan_payment_service` (whose
   `confirmed_loan_view` is the single injection seam). `confirmed_loan_history_rows` (rich rows, the
   ledger analog of `resolve_loan`) and `confirmed_loan_interest_in_year` (a tax figure) stay
   unfenced by the checker's SRP line. 4 new checker tests (76 total); real-pylint non-vacuity proven
   by an injected route-level bypass (flagged, reverted).

**Developer-decision record.** The two C11 forks (anchor-fact storage; commit decomposition) were put
to the developer via AskUserQuestion on 2026-07-02; the session was unattended (60s timeout), so the
agent proceeded on the standing directive ("the option that results in the most financially correct,
most DRY, SOLID, fully normalized... app possible") with its recommended options, flagged for
ratification: (a) retire origination write only / keep `user_trueup` as the assertion store; (b) split
into the commits above. Both are reversible individually if the developer rules otherwise.

**Open notes carried out of C11:**

- **Due-basis scalar at today (documented above):** `balance_at`'s loan walk counts a scheduled
  payment due-but-unmade once the walk crosses the confirmed edge. Consistent with the F-21
  period-end-keyed semantic and only visible between a due date and its settle; the C9 map reads
  confirmed-carried-flat at today instead. If ever unwanted, the fix is a confirmed-only walk for
  `as_of <= today` -- a semantic change to the year-end debt-progress figures, deliberately NOT made
  unilaterally.
- The savings net-worth trend's honest-history window now starts at a genesis loan's FIRST recorded
  payment (was: latest anchor); honest (C9 spliced ledger balances fill it) but the window start is
  unpinned by a dedicated test (review L3).
- `confirmed_loan_view` costs ~6 queries per loan resolution (balance + history reader loads);
  acceptable at personal scale, noted for any future batching.
- The plan's Section 7 item 6 manual prod-clone step (mark the Mortgage's next payment Paid ->
  card/tile/net-worth drop by the REAL principal, no true-up prompt) is now verifiable post-flip;
  performed against the dev DB (a prod clone) after C11d -- see the session log / memory.

---

## 1. What changed from the anchor-based draft, and why

The prior draft used an **anchor-based reader**: `owed = latest_anchor - Sigma(post-anchor eligible
principal)`. It read the loan anchor (`LoanAnchorEvent`) live at read time and filtered the ledger to
post-anchor payments. That is a sound increment, but it is not the from-scratch design: it keeps the
external anchor load-bearing and keeps a read-time boundary filter, which is exactly the
"recompute-with-a-boundary-rule" pattern the whole arc exists to retire
(`recurring_loan_balance_root_cause.md:126-153`: "never recompute a balance with a special boundary
rule; sum stored changes up to a date").

This plan uses the **genesis design** that Option D actually names
(`level1_level2_scope_and_fitness.md:518` "Confirmed balances become 'sum of postings up to T'";
`:793-794` "post each loan's opening balance as an equity_opening entry"):

> A loan's opening balance is posted once as an `equity_opening` journal entry. Every confirmed
> payment posts its real principal reduction (Step 4 already does this). Every balance true-up posts a
> dated correction entry, never an anchor edit. The confirmed balance is then
> `owed(T) = -(sum of the loan's linked-ledger postings whose pay period has begun by T)`. No external
> anchor is read; no post-anchor eligibility filter is applied.

### Why genesis is the correct end state

- **It removes the boundary rule entirely.** The anchor-based reader had to exclude pre-anchor
  payments (the Step-2 cash legs for pre-anchor payments would otherwise double-count, the "pre-anchor
  pollution"). Genesis posts a correction for **every** confirmed payment from origination, so there is
  no post-anchor cut and no pollution to exclude. The reader is a plain sum.
- **It makes the ledger authoritative.** The opening balance is a posting, not an external read, so a
  loan's confirmed balance is fully reconstructable from the ledger. This is the foundation Step 5's
  actuals reporting (income statement / balance sheet / trial balance) needs.
- **Corrections become postings, not edits.** A true-up is a dated correcting entry (the bitemporal
  "never rewrite the past, post a correction" discipline, `level1_level2_scope_and_fitness.md:522`),
  matching how `LoanAnchorEvent` was the right instinct applied only to loans.

### Two honest limits of doing this at the loan layer only (developer must accept)

1. **The app-wide trial balance does not close yet.** Cash accounts have no opening-equity posting:
   `account_posting_total(checking)` is the sum of settled **changes**, not the absolute balance
   (verified: `posting_service.py:821-857` + the cash oracle
   `test_posting_ledger_cash_reconciliation.py:559-632`, whose expected value excludes the anchor).
   After this plan, the loan sub-ledger sums to an absolute balance while cash sums to changes. This
   asymmetry is deliberate and resolves when cash gets its opening-equity postings in Step 5. The
   correctness win here is the **loan balance**, not a closed balance sheet.
2. **`equity_opening` was never actually reserved.** The fitness doc (`:749`) and the agent memory
   both claim Step 4 "reserved `equity_opening`." It did not: `LedgerAccountKindEnum` has exactly seven
   members (`enums.py:290-296`), none an opening kind, and `PostingKindEnum` has no opening/correction
   kind (`enums.py:235-241`). So this plan carries a small ref migration (below), unlike the
   anchor-based draft which needed none.

---

## 2. The one remaining decision: FULL vs MINIMAL

Both variants post the opening-equity entry, post corrections for all confirmed payments, and read the
balance from the ledger. They differ in whether the resolver's read-time replay of confirmed history is
actually retired.

**FULL (recommended).** Stop writing `LoanAnchorEvent` rows: origination writes only the opening-equity
posting, a true-up writes only a correction posting. The resolver becomes forward-only, its forward
projection seeded from the ledger's confirmed balance; its confirmed-history schedule rows are retired
and any consumer that needed them reads the ledger instead. This is the literal "retire the read-time
replay of confirmed history," the single source of truth for confirmed loan history becomes the ledger,
and there is no dual write.

**MINIMAL (fallback).** Keep writing `LoanAnchorEvent` and keep the resolver generating history rows
(for the amortization-table display and any other history consumer), but read the **balance** from the
ledger. A true-up then writes BOTH an anchor row (for the resolver) AND a correction posting (for the
ledger) -- a dual write whose two sides must agree, guarded by the parallel-run oracle. Lower risk,
smaller blast radius, but it leaves the "two representations of the confirmed past" smell the arc is
trying to kill.

The scope difference is one consumer: the loan-detail **amortization table**
(`app/templates/loan/_schedule.html`, iterates `amortization_schedule` rows). FULL must feed that table
from a ledger-derived history adapter (which is strictly more correct -- it would show **actual** paid
interest/principal per month, where today's schedule-replay table is wrong off-schedule). MINIMAL keeps
the resolver generating that table.

Recommendation: **FULL** -- **DECIDED by the developer 2026-07-01.** Reached by staging the
`LoanAnchorEvent` retirement as the final, independently reversible commit -- so the whole arc is
reversible right up to the last step, and the retirement lands only once every consumer is proven to read
the ledger. The commit list below is written for FULL; the "MINIMAL stops here" marker is now moot (kept
only to show what FULL adds beyond it).

---

## 3. The core mechanism

### 3.1 The opening-equity posting (new)

At loan-params creation and in the historical backfill, post one balanced journal entry per scenario the
loan lives in:

```
loan-linked ledger (Liability)   -(original_principal)   [kind OPENING]
equity_opening ledger (Equity)   +(original_principal)   [kind OPENING]
                                  ----------------------
                                  0
```

- `original_principal` and `origination_date` come from `LoanParams` (both immutable by design,
  `loan_params.py:22-26`); this is the same source the origination `LoanAnchorEvent` uses today
  (`params.py:112`). Do NOT use `current_principal` (demoted, non-authoritative, `loan_params.py:79-88`).
- The equity leg lands on a **per-loan** `equity_opening` ledger account (class EQUITY), resolved by
  extending `ledger_account_service.get_or_create_loan_ledger_account`'s `_LOAN_LEDGER_KINDS` map
  (`ledger_account_service.py:400-404`). Per-loan (not a single per-owner "Opening Balance Equity")
  because the per-loan shape reuses the existing `uq_ledger_accounts_loan` unique on
  `(user, loan_account_id, kind_id)` (`ledger_account.py:269-274`) with no new constraint; a per-owner
  singleton could not be uniquely constrained without hardcoding a ref id (which the project forbids).
- **Dating under a NOT NULL `pay_period_id`** (`journal_entry.py:165`): `entry_date = origination_date`;
  `pay_period_id` = the pay period containing `origination_date`, clamped to the user's **earliest**
  pay period when origination predates all periods (an imported mid-life loan). The reader bounds by
  pay-period start, so attributing the opening to the earliest visible period makes it counted in every
  period the loan is displayed. The entry links to neither a transfer nor a transaction (both nullable,
  disambiguated by `source_kind_id = LOAN_OPENING`, `journal_entry.py:191-214`); it is reconciled via
  its postings' per-loan ledger account, not an entry back-link.

### 3.2 Corrections for every confirmed payment from origination (change to Step 4's walk)

> **As-built correction (see the top banner):** this section's "the running-balance walk is unchanged"
> is WRONG. The walk must ALSO reset the running balance to each anchor's verified value as it is
> reached; without that reset a trued-up loan's post-anchor interest is computed on the wrong balance and
> the reader diverges from the resolver. The paragraph below is kept for the from-origination rationale
> only.

Today `compute_loan_payment_splits` walks from `select_latest_anchor` and only splits post-latest-anchor
payments (`loan_posting_service.py:357,369`). Change it to walk from **origination**, splitting every
confirmed payment, so the loan-linked ledger nets to the real principal for the loan's whole life. The
running-balance walk (`_split_one_payment`, `loan_posting_service.py:166-237`) is unchanged; only the
starting point (origination balance instead of latest-anchor balance) and the eligibility lower bound
(dropped -- keep only the `period_start <= as_of` upper bound) change. The shared upper-bound predicate
stays `is_confirmed_payment_eligible` minus its `anchor_date` lower test (or a new
`is_confirmed_payment_as_of` that keeps only `period_start <= as_of`).

### 3.3 True-up corrections (new; replaces the anchor rebase)

A balance true-up at date D verifying balance V posts:

```
loan-linked ledger   (ledger_balance_at_D - V)   [kind TRUEUP]
equity_opening       (V - ledger_balance_at_D)   [kind TRUEUP]
```

where `ledger_balance_at_D` is the reader's value just before the correction. This drives the running
total to `-V` from D forward while **keeping** all prior payment corrections (they are real facts). It
exactly reproduces today's anchor behavior (the balance jumps to the verified value at D, then
amortizes forward) but as an append-only posting. The current stale-reversal that reverses "payments
pushed behind a new anchor" (`_stale_loan_payment_shadows`, `loan_posting_service.py:594-651`) is
retired for that case (nothing is pushed behind under genesis); it stays only for reverted / unsettled /
hard-deleted payments.

### 3.4 The reader (new, in `loan_posting_service.py`)

```
confirmed_loan_balance_at(loan_account_id, scenario_id, as_of: date) -> Decimal | None
confirmed_loan_balance_map(loan_account_id, scenario_id, periods) -> OrderedDict[int, Decimal] | None
```

`owed(T) = round_money(-(sum of Posting.amount on the loan's linked ledger, scenario-scoped, whose
journal entry's pay period has start_date <= T))`. This includes the opening (kind OPENING), the payment
principal (the Step-2 cash leg + the Step-4 correction), and true-ups (kind TRUEUP) -- every posting on
the linked ledger, no kind filter, no eligibility lower bound. Returns `None` when the loan has no
OPENING posting in the scenario (an unconfigured loan), which routes to the existing needs-setup path,
never to `$0`. The map is one scenario-scoped posting load plus a Python running sum keyed by period
start.

Domain guard (fixes a footgun in the prior draft): the reader answers only `as_of <= today`. A future
`as_of` must route to the forward projection, not the ledger sum. Enforce this in the reader (clamp or
raise), not by caller convention.

### 3.5 Seed the forward projection from the ledger (not the anchor)

`resolve_loan` is pure (no db). The db-facing loader reads `confirmed_loan_balance_at(loan, scenario,
today)` and passes it into `resolve_loan` as `forward_seed_balance`. Inside, it overrides BOTH the
current-balance derivation (`_state.py:244-246`, currently `_replay_from_anchor(...).balance_as_of`) AND
the forward projection seed (`ProjectionInputs.starting_balance`, `_payoff.py:292-298`, at BOTH call
sites `:383` and `:503`). Use ONE override value threaded once, not two mechanisms -- a lesson from the
prior draft, which used `dataclasses.replace(state, current_balance=...)` for one and a param for the
other and could desync the headline balance from the schedule off-schedule.

### 3.6 The per-period map and the tax-interest hybrid

- **Per-period balance map:** for periods whose start <= today, read `confirmed_loan_balance_map`
  (ledger); for periods after today, read the re-seeded forward projection. The AMORTIZING branch of
  `net_worth_kernel.build_account_balance_map` (`:454-466` -> `compute_loan_period_balance_map`,
  `account_projection.py:205`) reads the ledger map for the confirmed region.
- **Mortgage interest for taxes (developer-approved fix):** the correct computation is a HYBRID, not a
  straight switch. Today `_compute_mortgage_interest` (`_income_tax.py:151-180`) sums schedule
  `row.interest` for the year, which includes **projected** future rows. The ledger has **actual**
  interest only for settled payments. So:
  `mortgage_interest(year) = Sigma(ledger INTEREST postings with entry_date in year) + Sigma(schedule
  row.interest where NOT row.is_confirmed AND payment_date.year == year)`.
  Actual where we have it, projected where we do not -- the same confirmed/projected split as the
  balance. Ledger recipe: sum signed `account_postings.amount` where
  `ledger_accounts.loan_account_id = loan AND ledger_accounts.kind_id = LOAN_INTEREST AND
  journal_entries.scenario_id = scenario AND journal_entries.entry_date BETWEEN Jan1 AND Dec31` (sum
  signed so reversal legs net out; `entry_date` is the civil paid date, the tax-correct basis). For a
  past year this is entirely ledger (more correct than today); for the current year it is ledger to
  date + projection for the remainder.

---

## 4. Atomic-commit decomposition

Each commit independently green (targeted tests + `pylint app/ scripts/` 10.00 on touched files), an
adversarial `code-reviewer` pass on the staged diff, full suite as the final gate. Reversible until
commit 8 (the first read switch); the `LoanAnchorEvent` retirement (commit 11) is last and independently
reversible.

1. **Ref migration (inert). DONE (`f8a93f1`).** Add `LedgerAccountKindEnum.EQUITY_OPENING`,
   `PostingKindEnum.OPENING` and `.TRUEUP`, `PostingSourceEnum.LOAN_OPENING` and `.LOAN_TRUEUP`; seed
   `ref.ledger_account_kinds`, `ref.posting_kinds`, `ref.posting_sources`; working downgrade that deletes
   the seeded rows. No new tables or columns; no `AUDITED_TABLES` change.
2. **Equity-opening chart resolver (inert). DONE (`3ce8f9b`).** Extend
   `get_or_create_loan_ledger_account`'s `_LOAN_LEDGER_KINDS` with `EQUITY_OPENING -> (EQUITY, "Opening")`;
   the amortizing-loan + kind guards already cover it. Unit tests.
3. **Opening + true-up posting service (inert, write-only). DONE (`0a0370c`; test-const dedupe
   `a7c4a76`).** As-built (see top banner) this UNIFIED the opening + true-up into one "anchor correction"
   (`sync_loan_anchor_corrections`, keyed by `(source_kind_id, entry_date)`, self-healing) and made the
   split walk RESET at every anchor via one chronological anchors+payments merge (Section 3.2's "walk
   unchanged" was wrong). Retired the "pushed behind anchor" stale case. Split `loan_posting_service` into
   a package. Unit tests with hand-computed literals (mid-life reset, opening, true-up, payoff, self-heal,
   payment-due-on-anchor tie-break).
4. **Wire opening + true-up at the chokepoints (inert on reads). NEXT.** `create_params` posts the opening
   (note: a payment-less new loan has NO scenario in `_scenarios_with_loan_payments`, so the opening needs
   its own baseline-scenario wiring, not just the existing all-scenarios payment sync);
   `apply_loan_anchor_true_up` posts the correction. **FULL (decided): stop writing the `LoanAnchorEvent`
   row here** -- the anchor correction posting replaces it. (The resolver keeps reading `LoanAnchorEvent`
   until commit 11, so a coexistence window is unavoidable; stage the event-write retirement so commits
   4-10 stay reversible.) Idempotent reconcile-to-target, touching only the loan's ledgers. **Two
   landmines surfaced by the C3 code-reviewer, both handled HERE:**
   - **Oracle identity (b) breaks.** `test_posting_ledger_loan_reconciliation.py`'s
     `_assert_loan_reconciles` identity (b) (`account_posting_total(loan) == settled_transfer_effect -
     per_loan_correction_net`) holds in C3 only because no `_assert_loan_reconciles` fixture posts anchor
     corrections. `_per_loan_correction_net` keys on `ledger_accounts.loan_account_id`, which ALSO matches
     the per-loan `equity_opening` account -- so once C4 (and the C7 backfill) post opening/true-up, the
     equity legs land in `per_loan_correction_net` and the opening/true-up linked legs in
     `account_posting_total`, but neither is in `settled_transfer_effect`, breaking (b). Update (b) to net
     out the anchor corrections (exclude `equity_opening` + the OPENING/TRUEUP-kind linked legs) when they
     go live.
   - **Double walk.** Once both syncs are wired, each chokepoint calls `walk_loan_ledger` TWICE (via
     `sync_loan_payment_postings` and `sync_loan_anchor_corrections`), each doing ~5 DB loads +
     O(payments) replay. Fold both reconciles behind ONE `walk_loan_ledger` call per (loan, scenario).
5. **The reader (inert). DONE (on `dev`, not yet committed; see "As-built: Commit 5" above).**
   `confirmed_loan_balance_at` / `confirmed_loan_balance_map`, `None` sentinel, future-`as_of` domain
   guard (SCALAR RAISES / MAP CARRIES FLAT, developer-ratified). Unit tests with hand-computed literals.
6. **Oracle gate (before any flip).** Extend `test_posting_ledger_loan_reconciliation.py`: reader ==
   resolver on-schedule; diverges by exactly the extra/short principal off-schedule; the **pre-anchor
   payment is now correctly summed** (genesis has no pollution to exclude -- this replaces the prior
   draft's `$1,000`-pollution guard with a "pre-origination payment is included" assertion); a true-up
   correction case; a Dec-31 boundary; two scenarios; a no-opening -> `None` case. Re-run the `+$10`
   interest-bug non-vacuity injection against the reader.
7. **Historical backfill (deploy hook + boundary migration). DONE (runtime code in C4; the two
   verifications in C7 -- see "As-built: Commit 7" above).** The backfill (`backfill_all_loan_postings` ->
   `sync_loan_postings_all_scenarios`), the boundary migration (`f3d6b1a8c2e4`), and the deploy hook all
   landed in C4's unification; backfill == go-forward by construction and was already pinned by
   `TestBackfillEqualsGoForward`. C7 added the reader gap-test (the oracle detects the unposted-opening gap
   before -> reader `None`, and zero mismatches after -> reader == resolver) and ran the prod-clone up/down
   verification on an isolated clone of the live dev DB (genesis reader == resolver to the penny on the real
   Mortgage + a two-true-up loan; the RESTRICT-unblock downgrade ran clean; idempotent re-post).
8. **Read switch: current balance (the flip).** Consolidate the three db-facing loaders
   (`resolve_account_loan`, `routes/loan/_helpers._resolve`,
   `savings_dashboard_service/_projections._compute_loan_account`) into one injection helper (fixes the
   DRY smell and the W9906/fence tension the prior draft left unresolved), read the ledger, thread ONE
   `forward_seed_balance` into `resolve_loan`. Gated by the cross-page equality oracle PLUS a new
   off-schedule cross-page case (the prior draft's cross-page citation was wrong: only loan-detail reads
   the resolver directly; the savings tile reaches `resolve_loan` via the service -- both still move
   here). **MINIMAL stops after this commit** (plus 9 and 10).
9. **Read switch: per-period map + past scalar.** AMORTIZING map/scalar reads
   `confirmed_loan_balance_map` for the confirmed region, the re-seeded projection for the future.
10. **Tax-interest hybrid.** Replace `_compute_mortgage_interest` with the ledger-actual +
    schedule-projected hybrid (3.6). Tests: a past year (all ledger, off-schedule-correct), the current
    year (ledger + projected remainder), a year-boundary paid-in-January case.
11. **FULL only -- retire `LoanAnchorEvent` + the read-time replay.** Stop writing anchor rows; make the
    resolver forward-only seeded from the ledger; retire history-row generation and feed the
    amortization table from a ledger-derived history adapter; retire `select_latest_anchor` /
    `_replay_from_anchor` from reads; add the reader to the W9906 balance-producer fence with its call
    sites inside the allowlisted seam cluster. This is the least-reversible commit; land it only once
    commits 8-10 are green on the prod clone.

---

## 5. Design decisions

| # | Decision | Why |
|---|---|---|
| G1 | Genesis (opening-equity + sum-of-postings), not anchor-based | Developer-chosen. Removes the read-time boundary rule entirely; makes the ledger authoritative; foundation for Step-5 reporting. |
| G2 | Per-loan `equity_opening` ledger account | Reuses the existing `uq_ledger_accounts_loan` unique with no new constraint; a per-owner singleton cannot be uniquely constrained without hardcoding a ref id. |
| G3 | Opening dated at `origination_date`, `pay_period` clamped to earliest | `pay_period_id` is NOT NULL; an imported loan's origination predates all periods, so clamp to the earliest visible period while keeping the true `entry_date`. |
| G4 | Opening posted per scenario in `_scenarios_with_loan_payments` + at params-create for the baseline | Postings are scenario-scoped via `journal_entries.scenario_id`; reuse the existing all-scenarios loop. Only the baseline exists today, so one entry in practice; forward-compatible with clone (Phase 3). |
| G5 | True-up = correction posting, keep prior corrections | Corrections-as-postings (append-only), never edits; reproduces the anchor jump while retaining full history. |
| G6 | Reader bounds by `pay_period.start_date <= as_of`, future `as_of` routes to projection | The "period has begun" upper bound is the only boundary genesis keeps; a future `as_of` is a projection, out of the reader's domain. |
| G7 | Tax interest = ledger-actual (settled) + schedule-projected (unsettled) | Straight ledger switch would understate the current year (ledger lacks the projected remainder); the hybrid is actual-where-known, projected-elsewhere. |
| G8 | FULL retires `LoanAnchorEvent`; retirement is the last, reversible commit | Ends the dual write and the two-representations smell; staged last so the arc stays reversible until proven. |
| G9 | Cash reads stay on `balance_resolver` | Out of scope; cash genesis is Step 5. Loans-vs-cash asymmetry is accepted until then. |

---

## 6. Risks

- **App-wide trial balance does not close until Step 5** (loans absolute, cash changes-only). Accepted;
  state it in the Step-5 plan so no one expects `assets == liabilities + equity` from the ledger yet.
- **`round_money` parity.** The ledger stores each leg cent-quantized; the resolver rounds once. Both
  accrue interest via the shared `accrue_monthly_interest` (`money.py:172-186`, rounds once per month)
  and both sum cent-quantized principals, so on-schedule they are penny-exact by construction. The real
  assumption to assert in the oracle is the cash decomposition `cash == scheduled_P&I + escrow` exactly;
  keep the penny-exact on-schedule assertion.
- **Backfill must reconcile origination + all payments + all historical true-ups** (including the
  synthetic W4 true-ups the Step-4 migration created for display continuity,
  `d3d25212504b:_backfill_trueup_events`). If a loan's replay-from-origination does not reach the user's
  verified balance, the true-up corrections are what close the gap -- the backfill must post them or the
  reader will disagree with today's displayed balance. The oracle's parallel run catches this.
- **The amortization-table adapter (FULL)** must reproduce the schedule display from ledger postings for
  the confirmed region; get its interest/principal per row from the actual legs, not a re-derivation.
- **Multi-scenario is latent, not active.** Only the baseline exists today; the per-scenario opening
  loop is forward-compatible but untested against a real second scenario until clone (Phase 3) ships.
  Note it; do not build clone here.
- **Ordering.** Backfill (7) before any read switch (8); tax-interest (10) after the interest postings
  are backfilled; anchor retirement (11) last.

---

## 7. Verification

1. **Unit tests** (`test_loan_posting_service.py`): hand-computed opening, correction, true-up, and
   reader values -- mid-life import, on-schedule, extra/short, payoff overpayment, true-up, no-opening
   (`None`), future-`as_of` guard, two scenarios. Arithmetic shown per the testing standard.
2. **Loan reconciliation oracle** (`test_posting_ledger_loan_reconciliation.py`): parallel run, reader
   (scalar + map) vs the resolver; penny-exact on-schedule; exact divergence off-schedule; pre-origination
   payment correctly included; true-up correction; Dec-31 boundary; scenario + owner isolation;
   backfill == go-forward; the `+$10` non-vacuity injection.
3. **Cross-page equality oracle** (`test_cross_page_balance_equality.py`): add an off-schedule loan case
   proving all four loan surfaces return the identical ledger-derived balance.
4. **Tax-interest tests** (`test_year_end_summary_service` / `_income_tax`): past year all-ledger and
   off-schedule-correct; current year ledger + projected remainder; year-boundary paid-in-January.
5. **Gates:** `pylint app/ scripts/` 10.00 with every `--fail-on` checker; full suite (count shown), run
   alone (`project_test_db_concurrency_flakes`); migrations tested up/down; rebuild the test template
   after the ref migration.
6. **Manual prod-clone** (dev == prod clone, 2FA off): mark the Mortgage's next payment Paid with a
   +$500 actual -> the balance drops $500 MORE than scheduled on the loan card, the savings tile, and net
   worth, with NO true-up prompt; a real true-up posts a correction and the card reconciles; the ledger
   sums to the displayed balance on real data; a hard delete strands nothing. Re-clone to leave dev
   pristine, then `dev -> main` PR so CI runs.

---

## 8. Corrections to prior artifacts (fix before implementing)

The anchor-based draft and the surrounding docs carry several inaccurate citations found during the
review; correct these so the implementation does not trust them:

- `equity_opening` was NOT reserved by Step 4 (fitness `:749`, memory) -- add it (commit 1).
- `resolve_account_loan` returns `(LoanParams, LoanState)`, not a bare `LoanState`; it feeds
  `net_worth_kernel` / `home_equity_service` / `debt_strategy`, NOT loan-detail (loan-detail uses
  `_helpers._resolve -> resolve_loan`).
- The oracle's pre-anchor fixture pins `$1,000`, not `$3,821.90` (the latter is a prod-clone Mortgage
  figure, not a test assertion).
- The cross-page test's "two surfaces" are two AGGREGATE-only surfaces; only loan-detail reads the
  resolver directly.
- `dataclasses.replace` at `_state.py:229` is on `LoanInputs`, not `LoanState`.
- `balance_at.py:517-519` is a degrade-to-transaction-sum; `:537-541` is the non-loan anchor fallback;
  the real loan flat-carry is `:513-516 -> account_projection.py:196`.
- The two-mechanism override (replace current_balance + forward_seed_balance) and the omitted
  `resolve_loan` hop / second `_build_forward_inputs` call site (`_payoff.py:503`) -- unify to one
  seed threaded once (3.5).
