> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# The shipped salary step `S11-c-2b`'s plan entry, condensed to a pointer under R-SAL103 on 2026-10-04

Ruling `salary:R-SAL103` ("Finished steps become pointers"): each finished salary step's full text
moves word for word to a history file as it ships, and `docs/plans/implementation_plan_salary.md`
keeps a one-line pointer naming its commit. `S11-c-2b` shipped at `c43200af` (the commit carrying
`Ships: salary:S11-c-2b`), followed on its branch by `60dbc309`, `81df649b`, `0913c125`, `bd07fa8f`
and `ea59b13d`. Its entry, as it stood in section 4 of that plan on the merge of its branch with
dev, is below, carried WITHOUT re-verification against the code. A live sentence may cite this
record for how something came to be, never as a plan of record.

`S11-c-2b`, as it stood:

      - [ ] **S11-c-2b -- each job says what its stub's gross holds** (**R-SAL102**; **SAL-592**): a NOT
            NULL boolean on `salary.salary_profiles`, false by migration, and a salary-form control; on
            a yes job the gross check adds non-taxable earnings, a no job keeping all of **R-SAL99**.
