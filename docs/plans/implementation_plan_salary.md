# The paycheck engine: what one paycheck is worth, stated once

**The arc that owns the paycheck engine** -- `paycheck_calculator`, `income_service`,
`calibration_service`, `payroll_basis`, the `routes/salary/` package and the tax services -- minted
2026-09-03 by the developer (**R-SAL1**) because four arcs edited the engine and none named it. Each
step, finding and ruling is a card in the tracker, `saltyreformed-labs/shekel-plan` (`CLAUDE.md`,
Development Status), whose board holds the order; "done" is `verification.md`, and what shipped is
archived under `historical/salary_*`. Sections 4, 5 and 7 held the steps, the findings' pointer and
this document's rules until the tracker took them.

## 0. Why this arc exists, and its own sequencing

**Four arcs edited one engine and none owned it.** `recurrence` carried R14, R15 and R18 because
their findings were found while tracing payroll deposits; `pay_calendar` carried C12 because the
engine's three readers each derived a calendar; `balance` carried X-at and X-av because the amount
model's INPUT side is the salary; and `balance:X-au-d` then measured three defects in the engine
itself and could name no step for any of them (**N-441**, **N-442**, **N-443**), which is the
ownerless finding the planning conventions' rule 1 retired, spelled as a whole subsystem. The
developer ruled on 2026-09-03 that the engine is a sixth arc with its own document, and on
2026-09-03 named it `salary` and moved C12 in with the others; N-443, the three spellings of the
paycheck projection, closed at R14's first leaf, which memoized the breakdown map, and C12 collapsed
the dashboards' readers onto it.

**Why each step sits where the board puts it, which is the board's to say and not this document's.**
The earnings-lines chain follows the bank_import production scope by `bank_import:R-JJ`, because
`R18` is what makes one payroll deposit one app row. `C12-a` decided the shape `balance:X-i1` waited
on (**R-SAL27**): the basis takes the pass's PRICER.

## 1. Root cause

**The engine's inputs are stated without the dimension that makes them a fact, and its answer is
spelled three times.** Both halves of `CLAUDE.md` rule 14, on one subsystem.

**The inputs.** An input that describes a MOMENT must carry it, or every derivation over it is
retroactive. The stored salary is an annual scalar for all time, so a raise and a typo correction
are the same edit (**N-237**), and it is stored in a UNIT the owner's stub does not state -- the
stub says `$3,526.00` a paycheck and `$91,675.00` a year, and `26 x $3,526.00` is `$91,676.00`, a
disagreement the app resolves silently (**N-391**). A calibration is one real stub on one real date;
its row stores that date (`pay_stub_date`) and only displays it; no pricing reader consults it
(**N-441**). A deduction's inflation escalation is anchored on the row's `created_at` (**N-240**). A
deduction's cadence is a biweekly COUNT used as a three-valued MODE (**F-21**), one reader ignores
it (**N-395**), and the earnings side has no lines at all, so an employer allowance with a cadence
becomes a separate income template that misfiles (**D59**). Two profiles on one template in one
scenario were priced by whichever `ORDER BY id` returned, until `X-av-1` made that state unstorable
(**N-294**, closed). A substituted tax year is never shown (**N-235**); a new year's brackets had no
door in the app; since `X-at-1` a release adds each year (**N-236**, closed).

**The answer.** `project_salary` is run by three readers over the same calendar --
`income_service.SalaryPricing._net_by_period`, `routes/salary/views.py` and
`routes/salary/cockpit.py` -- two of them keeping the whole breakdown and one keeping only
`net_pay`, so they cannot call each other; the shared leaf would be the BREAKDOWN map and it is out
of reach (**N-443**, **P62**). A fourth reader, the contribution tier, does not run the engine at
all and divides the annual figure by the paycheck count (**D45**). The engine derives its own
calendar where every other read-path consumer takes one (**P63**), and the module sat at pylint's
ceiling until R-F16 took it to 873; it is back to 994 (**P64**), so the room that argument rests on
is effectively gone.

## 2. Evidence

Every figure is dated and rests on the developer's own data; re-measure before citing.

| what was measured | result | when |
|---|---|---|
| the whole raise-blind feed against the engine, over all 63 saved paydays | `$1,646.84` understated (`$898.18` employee + `$748.66` employer); `$562.12` a full year. *Superseded `$137.51`, which was the employer half at ONE raise and did not compound* | 2026-09-03 clone, **D45** |
| one owner-level gross on a two-job owner, R-F16's reverted fix | a 39% swing that flips between renders with no data change | 2026-08-19, **D45** |
| the 2026-08-28 calibration applied to the seven RECEIVED March-June paychecks | `$2,454.10 -> $2,483.19`, `+$29.09` each, `+$203.63` over seven | 2026-09-02 clone, **N-441** |
| the same seven re-derived with the calibration removed, against their generated figure | `$2,473.38 -> $2,454.10`, `-$19.28`; the input is a calibration DELETED 2026-08-28 | 2026-09-04, **N-442** closed |
| all 12 settled paychecks priced under every calibration the data has held | 11 reproduce under the deleted row, the 12th only under the live one; none reproduces both | 2026-09-04, **N-535** |
| payroll deposits against the app's rows, seven of seven | `$0.04`-`$0.06` above the app; pricing at the stub's gross collapses it to `$0.00`-`$0.02` | 2026-08-30, **N-391** |
| live deductions carrying the `24` mode | 11 of 12 | 2026-08-19, **F-21** |
| the Health Insurance Allowance modelled as a 26-of-26 template | a `$100.00` income row on 2026-07-30 the employer does not pay, stopped only by an `end_date` | 2026-09-02, **D59** |

## 3. Target model

**One producer, every input dated, the period as the clock.**

1. **The breakdown map is the shared leaf.** `project_salary(basis, periods)` returns, per period,
   the gross, the taxable income, every deduction line, every earnings line and the net; the amount
   model reads a row's amount from it, the salary page and the cockpit render it, the contribution
   tier prices a deduction from ITS period's gross (**R-SAL2**), and the dashboards' "current
   paycheck" is one entry per PROFILE, summed (C12-b). A second walk is a cache with no column
   (**balance:R-IZ**).
2. **Every time-varying input is effective-dated, and the base is per paycheck.** The stored fact is
   what ONE paycheck pays, dated, with the annual figure derived beside it (**balance:R-HW(b)**,
   X-av); raises already are; a calibration applies forward from its stub's date (**R-SAL4**, S1)
   and calibrations ACCUMULATE, none destroyed (**R-SAL9**); a line's amount is dated too, its
   inflation compounding from its latest entry (S12).
3. **A line's cadence is a recurrence RULE against the pay calendar** (**R-SAL3**): a deduction or
   an earnings line names a rule, `NULL` meaning every paycheck, and the engine that already places
   recurring rows decides which paychecks a line lands on. `_deduction_applies_at` and the 26 / 24 /
   12 mode are deleted; an employer allowance is an earnings line with a cadence, not an income
   template (R15, R18).
4. **The period is the clock.** A projected paycheck is priced against its own period's inputs;
   nothing in the engine reads the process clock (`pay_calendar:C10` moves the five salary-route
   reads; the engine itself has read the owner's calendar since `balance:X-bh-1`).
5. **The tax law has one home, in the code, and a substituted tax year says so** (X-at).
6. **The engine is a package**, one private leaf per verb, the answer **P64** recorded at C12-a.

**What becomes impossible rather than checked**: a calibration restating a paycheck received before
its stub; a deduction priced off a profile not its own; a 24-of-26 benefit modelled as 26-of-26; two
readers of one paycheck disagreeing. Each is a state the model cannot express.

## 6. Alternatives considered and rejected

**Leaving the engine unowned**, each arc naming the step nearest its own finding: rejected
2026-09-03, because it is how three measured defects came to have no owner.
**Filing it under `pay_calendar`**: rejected, the calendar being one INPUT of the engine.
**A two-letter ruling prefix `R-SA`**: rejected, because the corpus-wide two-letter sequence would
eventually reach it, and a prefix that sequence cannot produce cannot collide with it.
