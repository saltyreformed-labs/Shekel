> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Recurrence R7c-b, as built, and the spans it archived (2026-08-15)

**Read-only history. Nothing here governs anything.** The live document is
`docs/plans/implementation_plan_recurrence_redesign.md`; this record exists so
that document's "Carried steps" section can hold POINTERS to what shipped
rather than the accounts themselves (`conventions.md` rule 5).

Archived at plan step **R7c-b**: its own account, the spans three new steps
(**R7d**, **R7e**, **R7f**) displaced from the arc document's line cap, and
the five ledger rows it closed. The five shipped `R-F*` accounts this file
first carried live in `recurrence_as_built_2026-08-15.md` instead -- `dev`
archived them the same day, and one account has one home. Rule 4 is explicit that a cap
is a forcing function rather than a ceiling sized to fit, and rule 5 that the
way back under one is to archive COMPLETED work -- never to trim a live step's
specification.

Each entry below is exactly what the live document carried, verbatim.

- [x] **R-D33 -- a date bound answers from occurrences.** `dd2a5a34`. Both closing bounds answer
      from whether the rule still OWES an occurrence, so "monthly until 31 December" and "monthly
      for 12 occurrences" cannot leave the obligations total on different days. Both carry the
      HORIZON guard that keeps an un-extended pay schedule from reading as a finished commitment.
      Measured $0.00 on the dev clone: the total is 11,066.16 before and after and no template
      changes inclusion. **D33 closes.**

- [x] **R-F8 -- the deploy's safety net stops lying (F-8, F-14, R-R14).** `2e63e4f9`, `8aeae48e`,
      `398c332c`. The pre-flight asks whether an image can resolve the revision the database is
      STAMPED at; "does the release add migrations" was directional and read a DOWNGRADE as safe,
      reproducing F-8. Dump-first, full-decode readback, behavioural gate. The symlink is installed,
      so the repo copy IS the live deploy path; the pre-fix hand-copy is kept at
      `/opt/docker/scripts/shekel-deploy.sh.prefix-2026-08-08.bak`.

- [x] **R7a-1 -- the Recurrence cell is one function over `(interval, unit)`.** `6fed14af`, on
      `dev`. `describe()` words a RESOLVED recurrence, so `(2, MONTH)` and `(1, WEEK)` already read
      correctly; `read_rule` makes the page resolve each rule ONCE; the Archived drawer gets a
      producer. **D17 closed.** Measured on production: 45 cells render, 2 move month (Anchor
      Disposal, Clothes) and 5 unauthored shapes reword -- see the commit.

- [x] **R7a-2a -- the paycheck count is derived per owner, not a constant.** `003e3657`. R7a-2 is
      TWO leaves, split with the developer 2026-08-11: the paycheck count is a fact about the
      SCHEDULE and the two-axis reading is a fact about the RULE, so the first ships alone and
      byte-identical. `PAY_PERIODS_PER_YEAR` is deleted; `pay_calendar.PayCadence` derives
      `round(365.2425 / cadence_days)` (section 4a), reached through two doors and refusing an owner
      who has stated none. **Opened F-16 and F-17.**

- [x] **R7a-2b -- the monthly equivalent is ONE expression.** `7c417b90`. Over `(interval_n, unit)`:
      `amount_to_monthly` is DELETED, `obligations_aggregator` computes
      `amount * units_per_year / (interval_n * 12)`, and the infrequent badge derives from the same
      pair. The seam is `recurrence.cadence_of` on the ONE table `resolve()` reads
      (`_frequency.py`); the `None` arm raises now.

- [x] **R7c-a -- the two-axis columns land, and nothing reads them.** `370a30cc`. Migration
      `f2a94c7e1b60` adds the five columns NULLABLE and backfilled, and the write door keeps them in
      step from the same `resolve` call that produces the phase. The SQL backfill is a second
      implementation, GRADED against that door over 3,080 rules; four planted defects fired it.
      Three adversarial reviews found what the commit enumerates. **D12 closes.**

## Section 4a as the live document carried it

## 4a. `PAY_PERIODS_PER_YEAR` (folded into R7a-2)

`PAY_PERIODS_PER_YEAR = Decimal("26")` (`app/utils/money.py:43`) is a magic number while
`cadence_days` is user-selectable 1..365, so every monthly-equivalent figure on `/obligations`,
`/savings`, and the Recurring surface is wrong for any non-biweekly schedule. It is NOT a separate
task: R7a-2 already rewrites `savings_goal_service.amount_to_monthly` from an 8-branch pattern
switch to four lines over `(interval, unit)`, and changing its input from a module constant to a
resolved per-user cadence is the same edit. Nine files reference the constant; overlap with the
balance arc is one file (`savings_dashboard_service/_metrics.py`, vs the X-x held branch).

**Derivation:** `periods_per_year = round(Decimal("365.2425") / cadence_days)`. Biweekly -> 26,
weekly -> 52, a monthly cadence -> 12. Resolved ONCE per request and threaded, never re-queried per
row.

**Why the rounded integer and not the exact rate.** A `365.2425 / 14 = 26.0888` rate was proposed
first and measured against the developer's own rhythm (anchor `period_index 0` = 2026-03-26):

```text
paydays per CALENDAR year:  2016-2025 all 26,  2026 = 27,  2027-2036 all 26,  2037 = 27
rolling-12-month count, every day of 2026-2027:  26 on 655 of 731 days (89.6%)
                                                 27 on  75 of 731 days (10.3%)
```

The 27-paycheck year is real (26 paychecks span 364 days, so the payday calendar slips ~1.25 days a
year and catches an extra payday every ~11 years; it is NOT a leap-year effect -- 2024 was a leap
year with 26). But it is a calendar-BOUNDARY artifact: 2026 has 27 only because Jan 1 and Dec 31
2026 are both paydays. A forward-looking rolling year -- which is what a monthly equivalent is --
holds 26 about 90% of the time. `26.0888` is asymptotically correct and matches no window the
developer budgets against; it would also shift every displayed figure +0.341% on migration day
(`+$25.91/mo` on the live every-period set) for no gained truth.

The only consumer where the long-run rate genuinely differs is the multi-decade retirement
projection: 0.34% over 30 years. Separable if it ever matters.

**And the calendar count is not even what the developer receives.** The 1 Jan 2026 payday fell on a
holiday and was PAID 31 Dec 2025, so 2025 carried the 27th and 2026 receives 26. The stored
`pay_periods.start_date` values are a NOMINAL 14-day walk; the real paydays shift off holidays and
weekends and the table does not model that. The developer has never observed a 27-paycheck year, and
the rounded integer is what matches lived experience -- ruled 2026-08-05.

Two things this surfaces, both pay-period concerns rather than recurrence ones, and both recorded in
section 5 as F-4 and F-5 (CLAUDE.md rule 6: report out of scope, do not fix):

- `pay_periods` stores nominal paydays. A holiday/weekend shift for the PAY SCHEDULE is the sibling
  of R8's business-day shift for recurrence occurrences, and would need the same holiday source.
  Scoping it is its own task.
- A 27-paycheck year is a real budgeting event (one extra Groceries at
  $500, one extra Data Manager paycheck at $2,473.38 of income). Surfacing it is a feature.

## The five ledger rows R7c-b closed

Verbatim as `ledger.md` carried them, closed by `900e761a`.

| arc | id | also | finding (one line) | worst measured | status | owner |
|---|---|---|---|---|---|---|
| recurrence | D21 | -- | the PERIOD unit's phase is read from the `offset_periods` COLUMN, which R7c drops | deriving it from the anchor instead diverges exactly where `_phased_period_anchor` falls back to the raw bound (fewer than `interval_n` periods remain past the bound): today's engine generates nothing there, an anchor-derived phase would generate from the first out-of-phase period | OPEN | R7c-b (the leaf whose readers take the phase off `starts_on`; R7c-a has already written the column) |
| recurrence | D10 | -- | a `Monthly First` anchor is horizon-dependent: extending the schedule can move it a month earlier | **none, MEASURED at R4b-2 and that is the finding's new content.** `_first_of_month_anchor`'s fallback -- the only horizon-dependent branch -- always answers a date strictly AFTER the schedule's last payday, so under `PERIOD_STARTING_ON_OR_AFTER` no occurrence derived from it can be placed on any period and no generated row can differ. Proven by argument, by a 3,390,012-pair sweep (1,935,097 of which reached the fallback, none an exception), and by the two `horizon_bound.*` baseline shapes plus `test_recurrence_resolution.TestTheHorizonDependentFirstOfMonthAnchor`. **No surface says so because no surface can see it** -- "say it on a surface" was ruled unwarranted 2026-08-08 on that measurement | OPEN | R7c-b (R7c-a's backfill has FROZEN the anchor into `starts_on`; this row closes when the readers stop recomputing it and read the column) |
| recurrence | D24 | -- | an `Every N Periods` rule's phase is READ from the start period when the schedule handed in contains it, and from the stored `offset_periods` column when it does not | none -- the write door has DERIVED the column from the start period on every write since R2c-1 (defect D1's fix), so the two can disagree only on a row written before that and never re-authored: zero on production, and all 46 live rules carry `interval_n = 1` where the phase is inert (measured 2026-08-08). Found by adversarial review of R4a, which is where the READ started agreeing with the write; the path-dependence is real -- the extend path hands over only the new periods, so it takes the column while create / regenerate take the derivation | OPEN | R7c-b (the same requirement D21 states from the anchor's side; R7c-c drops the column) |
| recurrence | D28 | -- | ruling R-R13's `starts_on` cannot carry a calendar rule's cycle PHASE. It is specified as "the opening validity bound, ONE meaning for every unit" while section 3 maps `month_of_year` -> `starts_on.month`, and those cannot both hold: the phase is a month RESIDUE class and the bound is not in it | **18 of the 24 live multi-month rules**, measured against production 2026-08-08 by driving `_effective_start` and `resolve` over all 46. Christmas (annual, November 1) has `starts_on = 2026-03-26` and no column left holding November, so the forward walk fires it 1 March every year -- generated rows and the projected balance, not a label. The 6 that survive are the ones whose authored month happens to be March. `res(anchor) == res(moy)` in every row: the ANCHOR carries the phase exactly, the bound never does | **RULED 2026-08-14 (R-R16): `starts_on` is the rule's FIRST OCCURRENCE, one meaning for every unit, and its position in the cycle IS the phase -- so `day_of_month` and `month_of_year` are DROPPED rather than renamed and the first occurrence is STORED rather than derived. Both sub-forks the row left are answered with it: no DDL has to state which phase columns a unit requires, because there are none, and the YEAR unit survives (R-R17). R7c-a has written the column; the row closes when the readers take the phase off it** | R7c-b |
| recurrence | D31 | -- | `_picker.CadenceOption` serves TWO consumers and carries the union of what they read, which is what its `too-many-instance-attributes` (8/7) disable is spent on: `recurrence_form.js` reads five ids and facts off `options_json`, the templates read three LABELS off the projections, and neither reads the other's | none -- the three labels are shipped to a browser that discards them, so the cost is payload and a disable whose stated rationale ("flatness is irreducible") an adversarial review measured as reducible along the consumer line | OPEN, found 2026-08-13 by adversarial review of R7b-2, recorded rather than fixed there: splitting the class changes the wire shape the script parses, which needs its own browser pass | R7c-b (it rewrites `_picker` wholesale when the form authors the new columns, so the split is free there and a separate diff here) |

## R7c-b's specification as the live document carried it

- [ ] **R7c-b -- every reader and the form move onto the new columns.**

`RecurrenceSpec` states one `starts_on` in place of `(day_of_month, month_of_year, start_date)`,
`resolve` reads the phase off it, and the form's "Day of Month" and "Month" controls are DELETED --
one date replaces three inputs, which is what makes the two-representation question unaskable rather
than answered. **D10, D21 and D24 close here**: the first occurrence stops being recomputed and
starts being read, so a horizon-dependent `Monthly First` anchor and a phase read off
`offset_periods` both cease to exist. **D31**, the picker's two-consumer value, is its form work.

**The INTERVAL does not move here, and an adversarial review of R7c-a caught this entry claiming it
did.** It read "the month interval widens to a free box" -- which cannot happen while the cadence is
stored as a closed-set pattern, because `encode_cadence(2, MONTH, ...)` has no pattern to write and
raises. The free box, the `interval_n` re-point and row **D32** all belong to R7c-c, with the
`pattern_id` drop that makes them possible.
**Until then a reader must take the interval through `decode_pattern` and never off the column**:
`encode_cadence` writes `1` for every pattern whose interval is in its name, so the four live
Quarterly and Semi-Annual rules read as MONTHLY at face value -- 12 occurrences a year where 4 or 2
are owed, across the whole projection. `TestTheIntervalIsStillTheClosedSets`, in the authoring
suite, is what turns red if this leaf moves a reader onto the column anyway; a paragraph is not.

**It must RE-BACKFILL before it switches the readers, and the reason is a real hazard rather than
belt-and-braces.** R7c-a's dual write refreshes the two-axis columns on every RULE write and on no
other event -- and `starts_on` for a rule with no stated bound is measured against the schedule's
opening payday, which a full rebuild moves. `loan_recurrence_sync._sync_loan_cadence` makes the
window plain: it returns early when the loan's own facts have not moved, so a schedule rebuilt
between the two leaves leaves that rule's `starts_on` at the value the migration wrote. Nothing
reads it, so nothing is wrong until this leaf makes the stored value authoritative -- at which point
it would FREEZE a stale first occurrence. Re-running R7c-a's own statement first costs nothing and
closes it.

**It carries the TIGHTEN and the CHECK the column needs**, both held back from R7c-a deliberately:
`NOT NULL` on the four, because it is the leaf where a NULL stops being invisible; and
`CHECK (end_date IS NULL OR end_date >= starts_on)` with its Marshmallow mirror, because it is the
leaf where `starts_on` is what the user TYPED and the pair is a two-field comparison the schema
layer can refuse without a calendar. Landing that CHECK earlier would fire it on a value the user
never entered -- **13** of the 46 live rules resolve to a first occurrence after 2026-08-14 (this
arc has carried "14" since 2026-08-08; re-measured, it is 13), so "stop this recurring bill" would
become a `CheckViolation` at autoflush.

**The `NOT NULL` is also what forces the test suite onto the write door**, and that is this leaf's
largest diff: ~40 test modules construct a `RecurrenceRule` DIRECTLY rather than through
`author_rule` (measured against R7c-a's first cut: 666 failures and 418 errors from five shared
fixtures). The flushed ones move onto the door, which is a real improvement -- they currently build
states `resolve` would refuse. **The TRANSIENT ones must NOT**: several exercise pure functions and
forcing a database and a calendar into them would be a worse test, not a stricter one.

**Two inherited findings belong here** because this leaf rewrites what the surfaces read. The
Recurring surface resolves a COUNT-bounded rule TWICE per row -- `obligations_aggregator` asks
`recurrence.has_ended`, which resolves and walks it, and `recurring_view._build_section` then calls
`read_rule`, which does it again; `$0.00` (one pure function, both answers agree) but the
redundant-producer shape this project treats as a DRY violation. And a create into a configured LOAN
still discards a typed "Starts on" silently, which needs a ruling: lock the control on a loan
destination, or say so in the help text.

**Three facts about the form this leaf must not undo**, carried from R7b-4 (whose fuller account is
`historical/recurrence_as_built_2026-08-14.md`):

- **A control that is DISABLED posts nothing, and one merely hidden still submits.** That is what
  the "Ends" lock, the "Starts on" lock and the transfer form's pay-period `<select>` all rest on --
  and getting it wrong cost a 500 the whole 9,293-test suite was green across, because a hidden
  "Starts on" submitted `""` into `TransactionTemplate(**data)`.
- **The loan bound locks ask `loan_recurrence_sync.owns_validity_window`, never `is_loan_payment`.**
  The second is NARROWER than it reads: neither of the developer's real loan payments carries a
  `loan_payment_settings` row, so a predicate keyed on one does not fire for either.
- **The create form DEFAULTS its opening bound, and that default is a money decision.** An empty one
  means "unbounded", and the create routes generate with no lower window bound: measured, a
  `$2,000.00` rent template created today wrote 5 backdated rows into pay periods that had already
  closed. `starts_on` being NOT NULL from this leaf is what makes the empty state unreachable rather
  than defended.

## Four SHIPPED index rows, retired from `steps.md`

Verbatim as the order table carried them. Each was already a pointer to an arc archive, so the index row was a pointer to a pointer; rule 5 retires it when the registry meets its cap.

| arc | id | also | what this step does | order | commit | starts |
|---|---|---|---|---|---|---|
| recurrence | R-F7 | -- | `_first_of_month_anchor` loses two provably dead guards, one of which commented a case that cannot execute; the 430-shape baseline stayed byte-identical. Closed **D11**. | SHIPPED | `5ac7ab4d` | -- |
| recurrence | R-F13 | -- | A baseline REGENERATION run can no longer report success, so a skip cannot read as a pass. Its other two holes no longer exist: `PlacementOutcome` and the `OccurrencePlacement` invariant died at `pay_calendar:C2-b2`. Closed **F-13**. | SHIPPED | `b97ec1c3` | -- |
| recurrence | R1-R3 | -- | Oracle, vocabulary, subtypes, write door, `Once` retired, forward engine. Archived to `historical/recurrence_as_built_2026-08-05.md`. | SHIPPED | `4b5c577b` | -- |
| recurrence | R4a | -- | The forward cutover, three commits, archived to `historical/recurrence_as_built_2026-08-08.md`. Closed **D3**, **D5**, **D22**, **D25**, **D7**. | SHIPPED | `1836a928` | -- |

## The four specifications those index rows pointed at

Retired with them, because rule 12 is symmetric: a specified step needs an index row. Each was already a pointer to an arc archive.

- [x] **R1-R3 -- oracle, vocabulary, subtypes, write door, `Once` gone, forward engine.** `4b5c577b`
      and the eight commits before it, archived under rule 5 to
      `docs/plans/historical/recurrence_as_built_2026-08-05.md` with the rulings taken for them
      (R-R4, R-R8, R-R10, R-R11). **Read it before R4b or R7c.**

- [x] **R4a, R4b-1, R4b-2 -- the forward cutover.** `1836a928`, `b4538d25`, `75346625`, archived
      under rule 5 to `docs/plans/historical/recurrence_as_built_2026-08-08.md`.
      **D3, D5, D22, D25 and D7 closed; D2 narrowed to the FIELD; D10 re-pointed to R7c.**
      **Read it before R5 or R7c.**

- [x] **R-F13 -- a baseline REGENERATION run can no longer report success.** `b97ec1c3`. Closed
      **F-13**. Account archived to `historical/recurrence_as_built_2026-08-15.md`.

- [x] **R-F7 -- `_first_of_month_anchor` loses two dead guards (D11).** `5ac7ab4d`. Both were
      re-derived from the code before deleting rather than taken from the archived proof: the scan's
      `earliest is not None` asks about a period's OWN month, so that period is in the minimand
      `pay_calendar/_searches.earliest_start_in_month` reduces; the fallback's re-ask could only be
      taken when the loop had already returned. The R1 baseline stayed byte-identical and
      `TestTotality` stayed green unchanged.

## 4a. `PAY_PERIODS_PER_YEAR` (folded into R7a-2, SHIPPED)

`003e3657` and `7c417b90`. The constant is deleted and `pay_calendar.PayCadence` derives
`round(365.2425 / cadence_days)` per owner, which is what makes every monthly-equivalent figure
correct on a non-biweekly schedule. Its full specification -- the derivation, the nine referencing
files and the ruled read-vs-write disposition -- is archived under rule 5 to
`historical/recurrence_findings_as_built_2026-08-15.md`.

## 5. Findings ledger

**Moved to `ledger.md`**, the one findings table for every arc. This arc's rows are the ones whose
`arc` column reads `recurrence`; a row's owner names a step in `steps.md`, whose specification is
section 4 of this document.

They moved because a finding is not arc-local: `P2` / `F-10`, `P3` / `N-123` and `P6` / `F-12` were
each one defect recorded in two ledgers, kept in step by hand, and one of those pairs went unnoticed
for months. The rules the table is graded against are `conventions.md`.

## 6. Alternatives considered and rejected

**Archived to `historical/recurrence_evidence_2026-08-11.md`.** The measurements and the rejected
options are a HISTORICAL RECORD: the rulings above state what was decided and the code states what
was built. Cite the archive for how a decision came to be, never for what is true now.

## 7. Document rules (GATED)

**Moved to `conventions.md`**, one copy for every arc. They were near-identical in three documents
and absent from the fourth.

`tools/plan_gate/` grades this document against them through a pre-commit hook scoped to it and the
CI step that runs the custom pylint checkers -- so EDITING THIS FILE is what runs the gate. This
document's own caps live in the gate's constants beside the other arcs'.
