> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# The pay calendar's C1-C2 span, archived out of the index (2026-08-25)

**What this is.** The ten `C2-*` rows that left `docs/plans/steps.md` and section 4 of
`docs/plans/implementation_plan_pay_calendar.md` on 2026-08-25, under `conventions.md` rule 5. They
had already been condensed to one line each earlier the same day; archiving them is the next legal
move, and it is what bought the room `C4`'s seven-leaf decomposition needed. **The COMMIT is the
record for every one of them** -- read the code it shipped, not this table.

**Why these rows and not the whole span.** Every row here is SHIPPED and none is cited by a live
sentence after the edit recorded below. FIVE shipped `pay_calendar` rows deliberately STAYED in the
index, and the reasons differ. `C2`, `C5a` and `C3` are structural: `C2` is one step under three
names with `balance:X-l` and `recurrence:R-F12` and rule 11 makes an identity class share a tick
state, `C5a` is the same shape with `recurrence:R-F10`, and `C3` is named by a live `starts` cell.
`C1` and `C2-f1` stayed for a different and less comfortable reason -- three of `tools/plan_gate`'s
own CONTROLS derive their specimen from them and have no other live pair to fall back on:
`_staging.a_prefix_trap` needs a shipped step that is a string prefix of an open one (`C1` against
`C10`-`C12`), `_staging.an_identity_class_with_leaves` needs a declared parent whose every leaf is
filed under a sibling's name (`balance:X-l` over `pay_calendar:C2-f1`), and
`test_arc_documents.TestAShippedStepIsAPointer` anchors on `C1`'s hash `f9d148fe` in the arc
document. Both `_staging` docstrings SAY this will happen -- "rule 5 archiving the completed span
takes these controls red again ... what is bought is the message and the filter, not permanence" --
so the reds are designed rather than surprising. They are still reds, and holding two finished rows
in a size-capped index for a control's benefit is the workaround those same docstrings describe as
the thing deriving was meant to end.

**The live sentence that had to move for this archive to be legal** (rule 5's second condition), in
`steps.md`:

- `pay_calendar:C9`'s `starts` cell read `NOW / pay_calendar:C2-e (shipped)`. `C2-e` is archived
  here, so the cell reads `NOW`. What it recorded -- that C2-e built the projection axis C9 projects
  the contribution tier onto -- is stated in C9's own specification, which is where the reason for a
  sequence belongs.
- Four `starts` cells inside the span named each other (`C2-b1`, `C2-b`, `C2-d` on `C2-a`; `C2-b2`
  on `C2-b1`). They left with their rows.

## The span

| id | commit | what it shipped, and what it closed |
|---|---|---|
| `C2-a` | `3cb3082f` | The one calendar VALUE, with nothing calling it: `PayCalendar`, three named questions, and a window that is a VIEW. Opened **P21**-**P25**. |
| `C2-b` | `fe365de1` | The DECOMPOSED parent of the recurrence cutover, ticked with `C2-b2`. |
| `C2-b1` | `90f2fbb7` | The calendar's last two questions, the cadence rule, and the one DB door. Opened **P28**. |
| `C2-b2` | `fe365de1` | The recurrence engine answers from the ONE calendar value; `PeriodCalendar`, `SchedulePeriod` and `RecurrenceScheduleError` are DELETED. Closed **P2** (= recurrence **F-10**) and **P25**; re-pointed **P26**, **P27**, **P28** to `C4`; opened **P34**, **P35**. |
| `C2-c` | `b8a72f6c` | Retire `balance_at/_cash_periods._PeriodSpans` so the cash view answers from the one calendar, keeping `None` outside the reported window as a VIEW question. Closed **P14**. |
| `C2-d` | `3e6cd4ec` | The filing cutover: both posting writers call the filing rule through one door. Closed **N-169**. |
| `C2-e` | `8143c6fe` | The projection axis is the OWNER's paychecks: `growth_engine.generate_projection_periods` and `SyntheticPeriod` are DELETED. Closed **P17**, **P20**-**P23**; re-pointed **P7** to `C2-f`; opened **P40**-**P44**. |
| `C2-f` | `4f134bf4` | The DECOMPOSED parent of "the readers answer from the calendar", split by READER over 60 call sites; all six `get_*` readers are gone and `earliest_recordable_day` stays, because it asks no "which period" question, only `min(first payday, today)`. |
| `C2-f2` | `531c1402` | The readers at a surface already holding a read pass. Five leaves, as-built in `pay_calendar_c2f2_as_built_2026-08-18.md`. |
| `C2-f3` | `4f134bf4` | The rest, and the module's last two readers. Five leaves, as-built in `pay_calendar_c2f3_as_built_2026-08-20.md`. **`C11` and `C12` were promoted OUT of it 2026-08-19**: neither removes a reader of a column `C4` drops, so leaving them here gated C4 -- and `C6`, `C7`, `C8` behind it -- on work C4 does not need. |

## What the span closed, kept here because rule 1 takes a closed finding out of the ledger

**P2** (= recurrence **F-10**), **P6**, **P12**-**P14**, **P17**, **P19**-**P25**, **P29**, **P32**,
**P36**-**P37**, **P43**, **P45**, **P47**'s duplicate half, **P48**, **P51**-**P52**, **P55**,
**P57**-**P59**, **P61**, **P64**'s calendar half, **P65**-**P66**, **P68**, **N-127**, **N-169**,
`balance:D42`, `balance:N-128`. The span also ticked recurrence **R-F10** and **R-F12**.

*Stated for the SPAN rather than per entry, which is what let it fit: an adversarial review of `C4`
measured a first cut of the 2026-08-25 condensation dropping this clause outright, orphaning thirteen
ids that `app/` docstrings still cite.*

## The fuller as-built records

`pay_calendar_as_built_2026-08-16.md`, `pay_calendar_c2a_c2e_as_built_2026-08-18.md`,
`pay_calendar_c2f2_as_built_2026-08-18.md`, `pay_calendar_c2f2d_as_built_2026-08-16.md` and
`pay_calendar_c2f3_as_built_2026-08-20.md`.
