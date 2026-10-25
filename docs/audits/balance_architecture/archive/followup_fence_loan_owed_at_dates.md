> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Follow-up: close the W9906 fence hole on `net_worth_kernel.loan_owed_at_dates`

**Status:** **CLOSED 2026-07-13.** Verified, then fixed at the root -- which turned out to be one
level deeper than this document originally proposed. The fix shipped as the balance-seam liability
view plus a new fail-CLOSED completeness checker (**W9909**), so the fence can no longer leak a
producer by omission. Full suite 7360 green; `pylint app/` 10.00/10 with every `--fail-on` symbol.

**Enabled by / depends on:** the Level-1 balance-at seam (`app/services/balance_at/`) and its
`shekel-balance-producer-bypass` (**W9906**) checker --
`tools/pylint/shekel_checkers/balance_seam.py`. See `implementation_plan_level1_balance_seam.md`.

---

## What was claimed, and what verification found

Every original claim held. Two things did NOT, and both made the problem worse, not better.

### Confirmed

- `net_worth_kernel.loan_owed_at_dates` IS a balance-at-T producer (it returns
  `{account_id: [owed at each date]}`, plotted directly as the horizon liability band) and was NOT
  in `_BALANCE_PRODUCERS`.
- `savings_dashboard_service._horizon` IS a consumer (not in `_BALANCE_SEAM_MODULES`), so a consumer
  reached past the seam into the kernel.
- The fence was silent: `pylint app/` reported **zero** W9906 findings. Adding the name to the
  producer set made it fire at exactly one site (`_horizon.py:467`) and nowhere else -- proof both
  that the hole was real and that the checker binds.
- No wrong number was on screen: `_build_sample_dates` yields `[today]` + strictly-future year ends,
  and the band passed `sample_dates[1:]`, so only future dates ever reached the producer.
- The "full fence, zero exceptions" invariant is real (`implementation_plan_level1_balance_seam.md`
  lines 71-73 and 363), so this genuinely violated a stated invariant.
- The register-bound checker tests ARE name-agnostic, so adding a producer name is auto-covered
  (`test_flags_every_guarded_producer_from_a_consumer`). ("Verify, do not assume" -- verified.)

### Corrected: the past-date hazard was understated

The original text said a past-date call "returns `current_balance` held flat, which is not the ledger
truth." **That is wrong whenever the loan has an OVERDUE payment.**
`account_projection.forward_balance_at_date` walks the schedule's UNCONFIRMED rows, and an overdue
(past-due, still unpaid) row deliberately stays in that walk -- the project's due-basis treatment
(`account_projection.py:358`). So a past-or-today date returns the balance net of a payment that was
**never made**, silently UNDERSTATING the debt. Demonstrated with the real functions:

```text
ledger truth today                     10000
PAST 2026-07-05  loan_owed_at_dates -> 9900   (a payment that never happened)
```

The failure mode was therefore not "stale but plausible" -- it was a wrong number in the direction
that flatters the user. That made the runtime guard (originally floated as optional "option 3")
mandatory, and it extended to `today` itself, not just the past. It is not a contrived case either:
the mortgage fixture in the existing test suite is in exactly this state (a loan originated a year
ago with no payments recorded carries ~12 overdue unconfirmed rows), which is now pinned by
`test_today_point_ignores_overdue_rows_that_would_understate_the_debt`.

### Missed: the root cause is the fence's DEFAULT, not a missing name

`_BALANCE_PRODUCERS` is a hand-maintained deny list keyed on function NAME. Its default is
**unfenced**: a new function born inside an allowlisted cluster module is invisible to W9906 until a
human remembers to list it. This shipped **twice** -- `investment_base_balance_map` (closed by
Level-1 Commit 10) and now `loan_owed_at_dates`. Two identical misses is a design defect in the
fence, not a lapse in diligence. Listing the name (this document's original plan) would have fixed
the instance and guaranteed a third occurrence.

### Missed: the seam had no shape for the job

The producer was written in the kernel because the seam offered only period-keyed maps and a scalar
at ONE date. The horizon needs "owed at ~25 calendar dates, for several loans," and the scalar would
re-resolve each loan once per date. There was nowhere in the seam to put it -- so this was a missing
seam concept, not a rogue call.

## What shipped

1. **A third seam shape: `balance_at.liability_owed_at_dates`** (`app/services/balance_at/_liability.py`).
   Answers every liability's owed magnitude at a list of FORWARD dates in ONE resolution pass, and
   owns **both** forward rules -- the amortizing schedule walk AND the no-forward-model flat carry
   (a revolving Credit Card, a loan with no `LoanParams`, or any liability with no baseline
   scenario). The flat-carry rule previously lived inline in the horizon consumer; a balance-at-T
   boundary rule living in a presentation module is precisely what the fence exists to prevent, so
   the seam took the whole rule, not half of it. It also owns the `abs` owed-magnitude convention and
   the today point.
   - It is the ONE public seam entry that does not `_require_scenario`: a missing baseline is not an
     error here but the degenerate case of its own rule (no loan is resolvable -> everything holds
     flat). Raising would force every caller to re-derive that flat carry -- the exact duplication
     the seam exists to kill. The rationale is pinned in its docstring and by
     `test_no_baseline_scenario_holds_every_liability_flat`.
2. **The producer's domain is now enforced, not documented.** `loan_owed_at_dates` raises
   `ValueError` on any date at or before today. `today` is excluded too, for the overdue-row reason
   above: the confirmed present is the resolver's `current_balance`, which the seam supplies, never a
   schedule walk.
3. **`_horizon._liability_band` is now pure assembly** -- select liabilities, ask the seam, sum. It
   no longer imports `net_worth_kernel` at all. Verified **byte-identical** on real dev data: all 25
   sample points match the pre-change band exactly ($192,941.56 today, amortizing to $0).
4. **The fence now fails CLOSED (`W9909`, `shekel-unclassified-fenced-export`).** Every PUBLIC
   top-level function defined in a fenced module must be explicitly classified as a producer
   (`_BALANCE_PRODUCERS`) or a deliberate non-producer. An unclassified one is an error **at its
   definition**, in the same per-edit hook run that created it. The next `loan_owed_at_dates` cannot
   be born silently.
   - Scope: the six engine-cluster modules AND the whole genesis loan-ledger package (whose reader
     fence had the identical fail-open default). Rulings are keyed BY MODULE, so a name ruled
     harmless in one cannot exempt a same-named function added to another.
   - `test_classification_sets_match_the_real_fenced_modules` parses the real source with astroid and
     asserts the sets exactly partition the modules' public surface in BOTH directions (no
     unclassified function; no stale entry from a rename).
5. **`balance_at` became a package** (`app/services/balance_at/`). It was at 975 lines against the
   1000-line cap, so ANY new seam entry breached it -- the same module-size wall `net_worth_kernel`
   hit before `net_worth_investment` was extracted. Split by view (`_inputs` / `_kind_correct` /
   `_cash_flow` / `_grid` / `_liability`) with `__init__` re-exporting the public surface. Zero
   consumer churn: every call site still reads `balance_at.balance_map(...)`. The W9906 allowlist
   prefix-matches, so the submodules stayed inside the fence automatically -- a case the checker
   header had explicitly anticipated.

## Hardened by the adversarial review

A `code-reviewer` pass over the finished diff found four things worth fixing; all four were fixed
before this was called done.

1. **The forward projection is now joined BY DATE, not by position.** The splice originally consumed
   the producer's list positionally, which was correct only because `loan_owed_at_dates` happened to
   build it in the caller's order with no sort and no dedupe. Nothing enforced that, and the
   expensive part of that function is the schedule walk -- so de-duplicating or sorting the sample
   dates (an obvious future optimization) would have silently shifted every point of the liability
   band, with no crash and no failing test. `test_projection_is_joined_by_date_not_by_position`
   passes the samples out of chronological order and would now fail loudly.
2. **One clock, chosen by the caller.** `liability_owed_at_dates` and `loan_owed_at_dates` now take
   the caller's `today` instead of each re-reading `date.today()`. With three independent clock
   reads, a request that crossed midnight between them would have seen its own index-0 sample as a
   PAST date and raised -- turning a benign race into a 500 on a page that previously just held the
   band flat. It also silently assumed the caller's dates were UTC-anchored, when the project's own
   policy is that user-facing dates are display-tz.
3. **`abs` is applied to the forward points too.** The view promised a positive owed magnitude at
   every date but only absolute-valued the today point. A schedule row's `remaining_balance` is
   non-negative, but the empty / paid-off fallback is the resolver's `current_balance`, which has no
   zero floor -- so an OVERPAID loan would have added its overpayment to the band today and
   subtracted it at every future point.
4. **W9909's scope now matches its claim.** It originally covered only `loan_posting_service._reader`
   while the prose claimed the ledger fence was fail-closed. The rest of the package already exports
   balance-flavored functions (`walk_loan_ledger`, `loan_balance_anchor_history`, ...), so a new
   reader born in `_display` or `_walk` would have reproduced the exact hole. The whole package is
   now scoped (19 public functions classified), and `walk_loan_ledger` -- the running-balance walk
   itself -- is now a fenced producer.

## One test changed (disclosed)

`test_accruing_kc_none_degrades_to_cash` patched `balance_at.balance_map` to force the documented
`None` return. After the package split, `_grid` looks that producer up through its defining module,
so the patch had to move to `balance_at._kind_correct`. **Only the monkeypatch TARGET changed; every
assertion is byte-identical.** The behavior under test (a `None` kind-correct map degrades to the
cash view) is unchanged and still passes.

## Residuals (not fixed here -- each needs its own decision)

1. **Loan resolved 4x per `/savings` render** (measured: 8 resolver runs for 2 loans). The horizon
   band was the fourth; routing it through the seam moved it behind the seam but did not remove it.
   See **`followup_redundant_loan_resolution.md`**.
2. **`DebtSchedule.current_balance` is a balance-at-today that no fence can see.** The W9909 ruling
   that `generate_debt_schedules` is a non-producer is TRUE today (verified: every out-of-cluster
   consumer reads only `.schedule` rows), but it rests on a fact about the current tree, not a
   property of the code -- W9906/W9909 bind on FUNCTIONS, and reading the attribute is invisible to
   both. See **`followup_debt_schedule_attribute_fence.md`**.
3. **The horizon answers "is this a loan?" two different ways.** The band now selects the amortizing
   subset via the canonical `classify_account` (inside the seam), while `_resolve_horizon_domain` and
   `_structural_milestones` still select via `ad.get("loan_params")`. They coincide on clean data, but
   an account whose type was changed away from a loan while keeping its `LoanParams` row (reachable
   through the account edit form) now gets a chart whose milestones retire a debt its band never
   retires. This one is a REGRESSION from this work, not a pre-existing smell. See
   **`followup_horizon_loan_predicate_split.md`**.
