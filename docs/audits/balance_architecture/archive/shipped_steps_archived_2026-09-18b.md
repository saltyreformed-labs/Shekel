> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# 3 shipped balance steps archived under rule 5 on 2026-09-18 (evening)

The balance README stood at 1,308 of its 1,330 lines with an upkeep entry (X-cs) to land against
the gate's 20-line headroom floor. These 3 SHIPPED steps of the X-bi-7 family are cited as a wait or an
alias by NO other row of `docs/plans/steps.md` (measured on the cut's tree with the gate's own
`blocked_keys` / `alias_keys` plus a scan of `(shipped` notes) and own no ledger row, so under
`docs/plans/conventions.md` rule 5 their rows leave the index and their README entries leave section 5,
verbatim below. Carried WITHOUT re-verification. `X-bi-7`, `X-bi-7b` and `X-bi-7d` keep their rows and
entries: `X-bi-7b` and `X-bi-7d` are cited as a wait or alias by live rows, `X-bi-7` as the origin of
BAL-492 / BAL-493 and of R-BAL20 / R-BAL21 / R-BAL26. The X-bi-7c leaves' own record is `shipped_steps_archived_2026-09-18.md`.

The README entries as they stood:

    * [x] **X-bi-7c** `4f15f222` -- the suite's one-off builder on 7b's producer, the DECOMPOSED parent
      split 2026-09-16 into the builder and four leaves by file group (the AST census in
      `tests/manual/census_hand_built_rows.py`: 228 link-less, 51 splat, 10 linked at the split; 30 at
      the close: 4 named stays, 16 bare CHECK builders, 10 linked); ticked with 7c-5. The five leaves'
      records: `archive/shipped_steps_archived_2026-09-18.md`.
      * [x] **X-bi-7d-1** `08230752` -- six bare CHECK builders and seven Core / raw-SQL writers
        (outside 7c's constructor census) take a rule-less definition each, dated on the paycheck's
        start; tests only; the two link-less controls in `test_template_row_needs_due_date.py` stay
        bare for 7d-2.
      * [x] **X-bi-7d-2** `829c2c26` (fix `9cf27a3a`) -- the migration: 34 definitions, 26 rows dated (**R-BAL25**),
        both cells dropped, the CHECK `= 1`, both keys RESTRICT (**R-BAL67**, **R-BAL73**); dump, grid and
        companion pages byte-identical; 7 Paid rows read 6-11 days late (**R-BAL22**); grid statements
        23 before and after. **MOVED MONEY**; disclosed **CC-352**. Closed **BAL-484**, **BAL-511**.

The index rows:

- **X-bi-7d-2** `829c2c26` -- Minted a definition per link-less row (34 on the 2026-09-18 restore), dated the 26 undated (`occurs_on`, **R-BAL25**), dropped both flag columns, re-cut the CHECK to `= 1` and both link keys RESTRICT (**R-BAL67**, **R-BAL73**); balance dump, grid and companion pages byte-identical. **MOVED MONEY**: the 7 Paid rows it dated read 6-11 days late (R-BAL22). Closed **BAL-484**, **BAL-511**.
- **X-bi-7d-1** `08230752` -- Gave six of the seven builders holding 7c's sixteen bare sites (via `bare_expense_template`) and seven Core / raw-SQL writers no constructor census sees a rule-less definition each, dated on the paycheck's start, so the cutover's `= 1` binds on them (a forced `= 1` runs its ten files green); the census stays 30; `test_template_row_needs_due_date.py`'s two link-less controls stay bare for 7d-2.
- **X-bi-7c** `4f15f222` -- The DECOMPOSED parent of the suite's ONE builder for a one-off row on `X-bi-7b`'s producer (`one_off_row_of`, the twin of `X-cf`'s `generate_row_of`), split 2026-09-16 into the builder and four leaves by file group; 289 hand-built `Transaction(` sites became 30 (4 named stays, 16 bare CHECK builders 7d re-cuts, 10 linked rows never this step's). Ticked with `X-bi-7c-5`.
