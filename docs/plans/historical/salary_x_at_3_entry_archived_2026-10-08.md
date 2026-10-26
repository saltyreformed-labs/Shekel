> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# The shipped salary step `X-at-3` condensed to a pointer on 2026-10-08

Ruling `salary:R-SAL103` ("Finished steps become pointers"): each finished salary step's full text
moves word for word to a history file and `docs/plans/implementation_plan_salary.md` keeps a
one-line pointer naming its commit. `X-at-3` shipped at `6f291f8f`, built by a cloud lane in one
commit carrying `Ships: salary:X-at-3`, whose message is its record. The entry, as it stood in
section 4 of that plan on the merge of the step's branch with dev (`cda8421f`), is below, carried
WITHOUT re-verification against the code. Two of its sentences did not come true as written: no
real no-income-tax state was listed, because the developer ruled the law lists NC alone
(**R-SAL128**) and the `$0.00` entry was built and tested on a made-up state; and the two-state
wording of **R-SAL91** was not put to the developer, because no second state landed: it is ledger
row **SAL-599**'s, held by a test that fails the day the shipped law lists a second state
(**R-SAL134**). A live sentence may cite this record for how something came to be, never as a plan
of record.

`X-at-3`, as it stood:

      - [ ] **X-at-3 -- the supported states** (**R-SAL78**): the law lists each one, a no-income-tax
            state as an explicit `$0.00` entry checked against a primary source (the developer names
            which); the profile form offers only those and refuses another, and the engine refuses a
            state the law lacks or a flat state with no rate. R-SAL91's one-sentence wording was ruled
            for ONE state; its two-state form ('... and NC tax uses 2026's and SC tax uses 2026's, ...',
            and the script's '..., except NC on 2026's and SC on 2026's.') is extrapolated and pinned by
            tests. X-at-3 puts it to the developer before a second state lands. In that form a state
            priced on the newest year is named nowhere. Closes **SAL-575**.
