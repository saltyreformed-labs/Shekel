> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# salary:S3-e-2 as built (2026-09-11)

**The payroll feed prices a payday ON DEMAND and carries no window** (ruling **R-SAL15**), so the
figure it INVENTED past the owner's saved calendar -- six extrapolation rules, each measured wrong
(**N-541**, **R-SAL10**) -- is gone rather than replaced by a seventh. Shipped at `a6af5b3c`
(branch `feat/salary-s3e2`), the MONEY half of the `S3-e` split (**R-SAL16**); `S3-e-1`
(`b8ee429a`) moved the two window-only questions off the feed first, in a commit where a moved
figure was a defect.

## The design, and the three rulings taken on 2026-09-11

`AccountPayrollFeed` is two RESOLVERS the loader builds over the read pass's per-profile pricer
(`income_service.ProfilePaychecks`), each `None` where the fact it answers does not exist for the
account: `employee` (what payroll puts in on a period's paycheck, folded off the engine's
`DeductionLine`s by `target_account_id`) and `gross` (the funding profile's gross on that
paycheck, **R-SAL5**). `load_payroll_feeds` prices nothing up front; it builds the pricers -- where
the tax series loads, so every query stays in the loader -- and closes the resolvers over them.

- **R-SAL19 -- a resolver takes the PERIOD, not its payday.** The engine prices a `DerivedPeriod`
  (it reads `start_date` and `period_id`) and every one of the six production callers held one, so
  the feed's input is the engine's input; keying by date was an artifact of the deleted
  dictionaries and would have meant re-deriving the period from a date the caller had just peeled
  off it, plus a fence around dates that are not paydays. `salary_basis()` dissolved into
  `gross_at`, which IS the growth engine's `period -> gross` hook. This revises one clause of
  **R-SAL18** -- *both period types keep serving `build_contribution_timeline`* -- because an ORM
  `budget.pay_periods` row spells its key `id` and cannot reach the engine; measured, no
  production caller hands one, so `PayPeriod.is_projected` was NOT added.
- **Both presence facts are DERIVED** from the resolvers' presence, one home each (rule 14):
  `is_payroll_linked` is the employee resolver's existence, `funds_employer` the gross resolver's.
  `S3-e-1` carried `is_payroll_linked` as a required field because two dictionaries could disagree
  with it; a resolver's presence cannot. **One state answers differently, and the adversarial
  review corrected which one**: `is_payroll_linked` was already carried off the deduction rows and
  reads the same on both sides; it is `funds_employer` that moved -- `bool(gross_by_payday)` read
  `False` for an owner who had NAMED an active funding profile but recorded no payday, and the
  resolver's presence reads `True`. Three readers turn on it (the `/investment` "funding job is
  not set" notice stops being shown to an owner whose job IS set, `calculate_investment_inputs`
  stops withholding `employer_params`, the seam builds a plan) and none prices anything, because
  there is no period; pinned by
  `test_paycheck_count_derivation...test_a_named_funding_profile_funds_the_employer_side_with_no_payday`.
- **N-542 and N-543 close here too**, beside the four the step row named: they are false
  sentences in the same deleted docstring, and their remedy column already read *deleted with the
  fallback branch*.

**R-SAL18 landed with it**: `DerivedPeriod.is_projected`, a named accessor over `period_id is
None`, is the timeline's transfer-average boundary; the `saved_through` parameter, both call
sites' wiring and `tests/test_arch/test_the_transfer_average_boundary_is_the_calendars.py` went.
The boundary case is graded against two mutations (add the average everywhere; add it nowhere),
each failing on the assertion it should.

**What went with the hold**: `_year_averages`, `_complete_years`, `_held_employee`, `_held_gross`,
`_first_payday`, `salary_basis(beyond=)`, `retirement_projection.build_employer_salary_basis`
(`/retirement`'s own December-of-year salary path, the `beyond=` arm), the `employer_salary_basis`
field on `RetirementProjectionContext` and `build_projection_context`'s fourth parameter, and the
28 tests whose subject was one of those (the 7 of the census file among them; 9 new cases stand in
their place, each named in the commit).

## What moved, measured

Two throwaway clones of production dumped 2026-09-11, migrated to head, read through four PUBLIC
doors identical on both trees (`tests/manual/measure_payroll_feed_on_demand.py`; 60 figures a run;
the balance seam at the last saved period as the CONTROL). The BARE clone is the developer's data
as it stands -- no deduction names an investment account, so the employee path is DEAD there and
only the employer half can move. The ARMED clone adds a `$250` 24-per-year deduction onto the
401(k), a `$100` deduction with a `$2,000` calendar-year cap onto the Roth, and three `$400`
transfers into the Roth, so both halves and the transfer-average term are live.

| figure | bare: before -> after | armed: before -> after |
|---|---|---|
| `/investment` 401(k) 40-year contribution line | `$243,490.01 -> $437,811.86` (**+$194,321.85**) | `$485,411.03 -> $679,078.27` (+$193,667.24) |
| `/investment` 401(k) 40-year balance | `$4,571,522.37 -> $5,567,639.64` | `$7,843,930.64 -> $8,833,112.21` |
| `/investment` Roth 40-year contribution line | unchanged (no feed) | `$323,639.69 -> $323,270.49` (**-$369.20**) |
| `/savings` Horizon, retirement band at 2049-12-31 | `$1,230,802.88 -> $1,358,194.65` (+$127,391.77) | `$2,435,690.01 -> $2,559,537.71` (+$123,847.70) |
| `/retirement` stored plan, projected total | `$922,239.43 -> $917,329.62` (**-$4,909.81**) | `$1,723,790.15 -> $1,716,773.33` (-$7,016.82) |
| `/retirement` +180 months, projected total | `$4,521,453.94 -> $4,493,799.88` (-$27,654.06) | `$8,570,511.53 -> $8,532,286.03` (-$38,225.50) |
| balance seam, last saved period (control) | unchanged, 3 accounts | unchanged, 3 accounts |
| current-period cards (`employer_per_period`, `employee_per_period`) | unchanged | unchanged |

The step row quoted `+$193,855.37` on the 2026-09-06 dump; the figure re-derived on today's dump is
`+$194,321.85`, and it is the developer's 5% employer contribution priced by the engine for every
payday of the 40 years instead of held at the last saved paycheck's gross. `/retirement` moves
LEAST and DOWN, as the step said it would, because that page already projected a salary path past
the horizon -- and its delta is THREE mechanisms rather than the one the step row named: the hold's
deletion, the replacement of the December-of-year `project_profile_salaries` path by the engine's
payday-priced gross (the AS-OF difference), and the basis profile changing from the owner's PRIMARY
profile (`gap.salary_profiles[0]`) to the account's FUNDING profile (`investment_params
.salary_profile_id`). On this data the two profiles are one row, so the third is `$0.00` here and
would not be for a two-job owner. **The Roth's `-$369.20` is N-541's over-read reproduced
exactly**: the hold paid the rest of 2028 at the 2027 average (`10 x $76.92 = $769.20`) where the
engine's cap leaves `$400` of the year's `$2,000`. Each run records its clock and database, and the
comparison REFUSES a pair that differs on either (a review finding: a payday boundary crossed
between two runs would move every axis and read as the mechanism).

## Findings closed here, verbatim from the ledger

| arc | id | also | finding (one line) | worst measured | status | owner |
|---|---|---|---|---|---|---|
| salary | N-541 (`salary:R14-b`'s passes six to nine 2026-09-04/05) | -- | **THE INTERIM TAIL RULE PAST THE SAVED CALENDAR IS WRONG IN BOTH DIRECTIONS**, accepted twice by the developer with the figures in front of him because `S3` deletes it. It OVER-reads a capped deduction: cadence 14, `$600` a payday against a `$1,000` cap, window `2027-01-15..2028-01-14` holds `$600`, annualising to `$15,600`. It UNDER-reads an uncapped one to `$0.00`: cadence 14, `$211.56` a payday, `2027-01-19..2028-02-29` holds `$0.00` against a true `$195.29` with `$5,712.12` of real in-window deductions (`models_employee` read True; `salary:S3-e-1` deleted that member, and the figures are what carried the claim). BOUND: both need a window whose span plus one interval each end fails to cover a calendar year -- below 52 biweekly paydays or 104 weekly, measured exactly. **A rule that never over-reads EXISTS and was DECLINED, not found impossible**: per observed calendar year, that year's priced total over the paydays the year really holds, minimum across years, 0 over-reads against the shipped branch's 24.5% over 8,820 shapes. Its cost is why it was declined -- on the module's own uncapped fixture it answers `$250` where the truth is `$500`. | `$0.00` on the developer's data: his 63-payday window reaches the branch on 0 of 3,650 anchors, and 11 of his 12 deductions are 24-per-year | **OPEN.** `S3`'s obligation is to DELETE the extrapolation, not to search for a seventh rule | S3 |
| salary | N-542 (`salary:R14-b`'s pass-ten enumeration 2026-09-05) | -- | **`investment_projection/_feed.py` CLAIMS THE TAIL RULE IS *exact only for a FLAT feed*, AND IT IS WRONG IN BOTH DIRECTIONS.** A flat CAPPED window whose cap has not bound over-reads `1.56x`, and the module's own stepped fixture is NOT flat and is held exactly. | `$0.00`; a false sentence, deleted with the fallback branch at `S3` | **OPEN.** Filed so a reader finds the correction rather than silence | S3 |
| salary | N-543 (`salary:R14-b`'s pass-ten enumeration 2026-09-05) | -- | **`_feed.py` CLAIMS THE EMPLOYEE AND EMPLOYER HOLDS *coincide only for a FLAT feed*, AND A FIXTURE IN THE SAME REPOSITORY REFUTES IT**: `test_investment_projection.py:301-307` is non-flat and holds `(195.29, 195.29)`. | `$0.00`; a false sentence, deleted with the fallback branch at `S3` | **OPEN.** The refuting fixture is committed and was never read against the claim | S3 |
| salary | N-544 (`salary:R14-b`'s pass-ten enumeration 2026-09-05) | -- | **`_feed.py` CLAIMS A 24-PER-YEAR DEDUCTION *skips one payday a month*; IT SKIPS THE MONTH'S THIRD PAYDAY, TWICE A YEAR AT CADENCE 14.** Twelve skips would leave fourteen payments and contradict the *24-per-year* the same sentence asserts. | `$0.00`; a false sentence, deleted with the fallback branch at `S3` | **OPEN.** The arithmetic contradicts itself within one sentence | S3 |
| salary | N-545 (`salary:R14-b`'s pass-ten enumeration 2026-09-05) | -- | **`_feed.py` CLAIMS THE TAIL RULE *always over-reads*; IT HAS 218 COUNTEREXAMPLES ACROSS 8 OF CADENCES 1-45.** It holds at cadences 7 and 14 only, and it is defeated by the very `periods_per_year` error the sentence beside it warns about. | `$0.00`; a false sentence, deleted with the fallback branch at `S3` | **OPEN.** A universal claim measured on the two cadences its author had in mind | S3 |
| salary | N-546 (`salary:R14-b`'s pass-ten enumeration 2026-09-05) | -- | **`_feed.py` STATES ITS MISS CLASS AS THE WHOLE OF IT; 198 OF 28,561 MEASURED MISSES IS THE MEASURED PART.** The sentence reports a censused subset as though it were the population. | `$0.00`; a false sentence, deleted with the fallback branch at `S3` | **OPEN.** A census reported without its unit, which is the class rule 3 exists for | S3 |

## The adversarial reviews

One neutral adversarial review (a fresh `code-reviewer` subagent over the design and the
uncommitted diff, 2026-09-11), graded against the fix's own claims. Ten findings; every one but the
first was fixed in the same commit, and the first resolved itself:

- **High: the `_derive.py` ceiling.** The property took the module to 1024/1000, and ruling
  **R-IR** puts a split on the breaker. Resolved by sequencing rather than by a second split:
  pay_calendar's `C17-b-1` (PR #311) moved the projection half into `_projection.py` first, and
  the property was re-applied on the 773-line survivor after one resync.
- **Medium: a false narrative in a test comment.** The rewritten no-payday case said the empty map
  had read `is_payroll_linked` as unlinked; at HEAD the flag was CARRIED off the deduction rows
  and read `True` there too. What an empty calendar moved is `funds_employer`; the comment was
  corrected and a case with a named funding profile and no payday added.
- **Medium: two stale paragraphs in `_horizon.py`** still describing a held paycheck past the
  calendar and a `/retirement` salary path that no longer exists. Rewritten.
- **Medium: the money figures were quotes** until the shipped harness reproduced them, and the
  harness could not refuse a before/after pair taken on different days or databases. Both runs
  were redone from `584b30fa`'s tree and this one on the same day; the harness now records its
  clock and database and refuses any other pairing (proven to fire across the two clones).
- **Medium: the no-query test was blind to `raise_type`**, the one relationship the engine reads
  only on a raise's own month, because its fixture had no raise. A recurring merit raise every
  July was added and the case now fails on a loosened `lazy="joined"` there too (mutation run).
- **Low: R-SAL19 cited with no registry home** -- this document and the rulings row are that home.
- **Low: `_asset_contributions.py` still said "the feed's hold rule never engages here"**; fixed.
- **Low: four tests handed ORM `PayPeriod` rows** to readers that now take a `DerivedPeriod`
  (passing only because an absent feed never reads the period); moved onto the calendar's own.
- **Low: behaviour below the opening bound changed silently** -- a period before the first payday
  is now refused by the engine's `_month_ordinal` where the dictionaries held their earliest
  figure backward; unreachable from production, and the feed's docstring now says so.
- **Low: a fourth `build_projection_context` caller** in `test_savings_dashboard_service.py` still
  passed four arguments; the full suite caught it before the review did, and a fifth in the manual
  harness `verify_reader_baseline.py` was caught by the harness gate (the E1121 class it exists for).

The review also verified clean: no production caller hands an ORM row (all eleven call
expressions traced), the resolver fold cannot double-count or KeyError, `project_profile_salaries`
and `gross_per_paycheck` keep live readers, Transfer Invariants and IDOR untouched, every figure
`Decimal`, scope held to the step.
