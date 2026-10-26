> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# Thirteen shipped recurrence steps, as built (archived 2026-09-02)

Archived out of `implementation_plan_recurrence_redesign.md` on the developer's ruling
that an as-built is unnecessary where it survives in the committed code. Each step's
commit is the record; what follows is the argument that produced it.

- [x] **R17 -- a generated row IS its occurrence, not its paycheck.** `4e8b40b3`, migration
      `c8e5a2f31b47`, ticking `pay_calendar:C5b` with it. Closed **D57** (`$1,482.93` before,
      `$0.00` after) and **P16**. `OccurrenceClaims` states what a row claims: its `occurs_on`, or
      its PAY PERIOD where that is NULL -- which this specification had backwards, and the unarchive
      door prices at `$20,500`. Both indexes re-keyed; the **D19** refusal and its exception,
      handler and template gone.

- [x] **R7c -- THE CUTOVER, the DECOMPOSED parent of three leaves.** `ee35bca7`, ticked with
      `R7c-c`, its last leaf. Split 2026-08-14 (**R-R18**) as expand / migrate / contract, so the
      destructive DDL came LAST. Account and its five rulings:
      `historical/recurrence_r7cc_as_built_2026-08-16.md`.

- [x] **R7c-c -- the closed set dies.** `ee35bca7`, migration `d9f5c1a48b73`, account
      `historical/recurrence_r7cc_as_built_2026-08-16.md`. Closed **D6**, **D32**, **D37**, **D38**,
      pay_calendar **P11**; opened **D39**. **Still owed**: `R5` deletes `compute_due_date`, and the
      WEEK unit `R8-b` frees is where that migration's SQL and its Python twin part.

- [x] **R7d-a -- an uncovered installment is priced by the DEFINITION.** `89cb0c1d`, ruling
      **R-R33**. The ESTIMATED tier asks `standing_installment_cash` what the loan's own recurring
      payment states for THAT installment, and states its escrow beside the cash. `$0.00` on
      production, 776 forward figures byte-identical. Closed the loop the first two designs died on
      and the horizon switch with it; opened **N-352**, **D47**, **P76**.

- [x] **R7d-b -- the RESOLVER exists, and nothing reads it.** `0462dc38`, ruling **R-R35**.
      `loan_payment_window(template, ctx)` answers `ClosesOn` / `Indefinite` / `EMPTY`, the sum type
      a nullable date could not carry. R-R35 took its SUBJECT from the loan to the DEFINITION; its
      VALUE still reaches the tie-break through `standing_payment`, D47's surviving half. `$0.00`.
      Re-scoped **D47**; opened **D48**, **D49**, **D50**.

- [x] **R7d-c-1 -- the generate pass CARRIES the read pass, and the ROUTE opens it.** `61d81c7f`,
      ruling **R-R38**, account `historical/recurrence_r7dc1_as_built_2026-08-27.md`.
      `GenerationSchedule` takes a `BalanceContext` and DERIVES its calendar, and the five doors
      that create a pay period RECORD and return, so `routes/_period_population` opens the pass
      between the paydays and the rows. `$0.00` through all three generate doors. Closed **D58**,
      found by that caller census: the GENERATE route populated nothing.

- [x] **R8-a -- the offer set stops being gated on a derivation that was deleted.** `87e2c5b9`,
      account `historical/recurrence_r8a_as_built_2026-08-16.md`, which holds four rulings
      (**R-R23**-**R-R26**) and every measurement -- **read it before R8-b, R8-c, R8-d or R11**.
      Closed **D20**, opened **D40**.

- [x] **R10-a -- a regeneration MAINTAINS its rows.** `5fc13cdb`, ruling **R-R19**, closed **N-292**
      (`$499.82` of purchases destroyed by a rename, measured; 0 after).
      **Two things a later step must obey.** The repeat refusal takes a NARROWER blocking set on the
      maintain path, because a maintain pass rewrites the rule's own row rather than adding beside
      it. And `recurrence_engine` is a PACKAGE from here, with `DerivedRowFields` the ONE statement
      of a generated row's derived columns -- a new one belongs there, not in a write path.

- [x] **R10-b -- the transfer engine onto the same shape.** `ea776528`, rulings **R-R19** and
      **R-R32**; both stated premises were measured false first, and the defect that opened is
      closed by the same commit, which is its record. **Two things a later step must obey.**
      `DerivedTransferFields` carries `amount`, so **X-au-f** must remove it when a generated
      transfer's amount goes NULL (N-293); and the maintain DECISION is SHARED, so a new arm goes in
      `_recurrence_common`.

- [x] **R-F10 -- delete the gap machinery.** `fe365de1`. Closed **F-10**; the LOSS survives as
      `pay_calendar:P16`. Account archived to `historical/recurrence_as_built_2026-08-15.md`.

- [x] **R-F12 -- one `PeriodCalendar`, not three period-containing searches.** `4f134bf4`. Closed
      **F-12**, as `pay_calendar:C2` / `balance:X-l`; an AST census found SIX, not three.

- [x] **R16-a -- the forward fold charges TIME, not payments.** `e8baa3c0`, ruling **R-R36**.
      `apply_payment_cash` allocates against charges already standing; `loan_plan` returns
      `LoanForwardPlan(payments, charges)`, walked merged with a charge before any payment sharing
      its date. `$0.00` over 776 figures and 4,000 differential trials; the corpus could not see it,
      so the controls are the two-payments-in-one-month tests. Opened **D51**-**D55**.

- [x] **R16-b-1 -- the occurrence walk stops TRUNCATING at the saved horizon.** `1b818135`.
      `PayCalendar.paychecks_from` composes `current_and_future_window` with a `projected_paychecks`
      continuation `axis_window` stepped a second copy of. 255 dates against 62 through
      `2036-01-01`, and every production READ DOOR byte-identical over all 43 live rules HEAD vs
      branch (clone, 2026-08-27); the 776 loan figures prove nothing here, `balance_at` not
      importing this package at all.
