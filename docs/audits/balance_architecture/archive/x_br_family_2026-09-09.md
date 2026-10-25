> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# The X-br family: the fresh container per run (archived 2026-09-09)

**Why this span and why now.** The balance README stood at exactly its 20-line headroom floor
(1260 of a 1280 cap) when `balance:X-bl-2a` split `X-bl-2` into a shipped leaf and a live one, and
rule 4 forbids raising a cap when it binds. Rule 5 archives a COMPLETED span, and this is one: the
container-per-run work is five shipped entries under one container, and nothing in it is waiting on
anything. Archived on the developer's call (2026-09-09), who was asked because rule 4 makes a
binding cap a question rather than a paragraph.

**Every entry is condensed to rule 5's one line -- its id, its commit and what it closed -- and
carried WITHOUT re-verification**, which rule 5's third condition requires be said. The COMMIT each
names is the record; read the code it shipped, not this file.

## Rule 5's second condition, checked rather than assumed

Five live README sentences cite these ids, two of them inside the still-open specifications of
`X-bt` and `X-bs`. They were read one at a time before archiving, and every one is a citation of
HOW SOMETHING CAME TO BE rather than a dependence on the archived prose: each names a step ID, the
ids stay in `steps.md`, and rule 15 makes this file exactly where a reader following one lands. The
live sentences are `X-bt`'s "**X-br-3** fixed that in one home", its "(**X-br-3**'s M6)", and the
`N-461` row's "occurred during `X-br-4`", "has recurred since `X-br-1`" and "the half `X-br-4` did
not reach".

**One obligation was verified as surviving elsewhere before being carried**, because an obligation
that lives only in an archived line is an obligation nobody will meet. `X-br-1`'s *"the cache key is
an OPTIMISATION and verification runs on EVERY invocation, so a stale image is refused rather than
trusted"* is stated in `scripts/build_test_db_image.py`'s own module docstring, point 3 (*"THE CACHE
KEY IS AN OPTIMISATION, NOT THE CORRECTNESS ARGUMENT"*) -- which is where `X-br-1`'s entry always
said the specification lived. `X-br-4`'s *"N-459's remaining site, the deploy fixtures' `-p 0:443`,
stays with **X-bs**"* survives as a live ledger row with a live owner, which is rule 5's first
condition working as intended.

## The span

| id | commit | what it did and closed |
|---|---|---|
| `X-br` | `6a3eb135` | THE FRESH CONTAINER PER RUN (ruled 2026-09-04), the container over the four leaves: every fence the suite carried existed because ONE postmaster served every worktree. Ticked with `X-br-4`. |
| `X-br-1` | `b1ffc9b6` | The test template baked into a tagged, self-verifying image (PR #247, merge `418695b2`); `scripts/build_test_db_image.py` IS the specification. |
| `X-br-2` | `7c739495` | A run gets its own cluster, pytest as a CHILD so there is an after in which to remove it, INT and TERM trapped with EXIT. Measured FASTER than the shared cluster. |
| `X-br-3` | `8ee74b95` | The harness got a daemon of its own, `DOCKER_HOST` selecting a rootless one, so per-run containers stopped landing on the daemon running production. 25 passed, 3 skipped -- those 3 are `N-459` / `N-460`, owned by `X-bs`. Four pre-ship clauses were refuted by the ship; the corrections are in `docs/test-harness-isolation.md`, which says "Do not restore them here." |
| `X-br-4` | `6a3eb135` | The slot, the probe, `RESTART_TEST_DB`, `TEST_DB_PREFIX` and the `test-db` service are gone; the private cluster is the only path. Closed `N-457`. Its own pre-ship sentence was WRONG about one of the five fences -- the slot's CONTENTION hazard survives a per-run cluster, so the LOCK went and a NOTE replaced it (`R-BAL1`), and the bake port became caller-chosen (`R-BAL2`). |

## What left the INDEX with it, because rule 12 is bidirectional

`steps.md` and the arc documents agree in BOTH directions (rule 12), so a span
archived out of the README leaves `steps.md` too -- the same route `X-f1`'s
fourteen leaves took, which rule 13 records. The five rows went with these five
entries, and `steps.md`'s declared counts moved with them: **300 steps -> 295**,
the graph **111 edges over 90 rows -> 110 over 89**. The ready count is unchanged
at 75, because a SHIPPED row was never startable.

**One dependency key had to be dropped, and it is named here rather than left to
be discovered.** `X-bt`'s `starts` cell read `NOW / balance:X-br-3 (shipped)`.
Rule 13 requires every key in that column to name a real step, so once `X-br-3`
left the index the key was dangling. It was removed rather than the archiving
abandoned, because the constraint it recorded is DISCHARGED -- `X-br-3` shipped
at `8ee74b95` -- and the record of it is this file. `X-bt`'s cell now reads
`NOW`, which is what the gate derives for a step nothing unshipped blocks.

Four `ledger.md` rows cite `X-br-3` / `X-br-4` in their SOURCE column (`N-459`,
`N-460`, `N-461`, `BAL-469`). Those are untouched and correct: rule 1 binds the
OWNER column, every one of them owns a live step, and citing the trace that found
a defect is exactly the "how it came to be" this file exists to answer.
