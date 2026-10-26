# Process lessons

**One copy of what this project has already paid to learn, shared by every arc.**

**One line each, and the line is the LANDMINE, not the story.** A lesson earns its place by having
COST something -- a rebuild, a wrong figure, a review round -- and it names the step or finding that
paid. The narrative stays in that commit: this file only works if it is read straight through, and
prose is what stops that happening. An append-only file nobody finishes reading loses its lessons as
completely as deleting them would.

**A lesson MECHANIZED into a gate is RETIRED from here**, its line moving to that gate's own
rationale, where a reader meets it at the moment it fires.

**Never record STATE here** -- what shipped, what is next, what production runs. That is the
tracker's and git's, and a sentence about it here would be a second answer beside no reconciler.

## The rule itself

- **An argument a caller can get wrong is a defect, not a contract.**
- **A DRY refactor of a PREDICATE can move money.** Two spellings that agree by reading are two
  answers until one is deleted.
- **When two figures PARTITION a set, write both halves from ONE predicate**, or the boundary drifts
  and both halves look right.
- **CENTRALISING one clock WIDENS the gap to every clock it did not centralise.** `C2-f2d-1` closed
  a `$4.18` split and opened a `$2,173.38` one, an `as_of=None` default a whole producer away.
- **When a rule is re-keyed, the complement must move with it**, or replay and projection stop being
  exact complements.
- **When two sides of one problem have different SHAPES, the loose side is where the next hole is.**
- **A RULING THAT FALSIFIES A PREMISE MUST BE GREPPED FOR THE PREMISE, NOT THE SYMPTOM.** X-f3b let
  a Projected row hold postings; six write paths were safe only because it could not, and review
  found four. Search for the SENTENCE the old code relied on.
- **A BOUND JUSTIFIED BY "THIS DIRECTION IS CONSERVATIVE" INVERTS WHEN WHAT IT BOUNDS CHANGES
  MEANING.** `TransactionEntry.settled_on` was unbounded above because a forward day held the budget
  back; the same day RELEASES it once a posting books cash (X-f3b / R-FM). Re-read every bound whose
  reason is a direction, not a fact.
- **WHEN TWO VALUES HAVE ALWAYS BEEN EQUAL BY CONSTRUCTION, THE CODE THAT COUPLES THEM DOES NOT
  EXIST.** Ask which values a field has silently equalled before widening what it holds (X-f1c4c).
- **Ask what a producer says the SECOND time.** One correct on the ordinary path can be inverted on
  the correcting one, and only the correcting path is worth a test (X-f2-a, `-$45.86` vs `-$92.29`).
- **A MECHANISM THAT HAS ONLY EVER BEEN RE-KEYED HAS NEVER BEEN DESIGNED.** Ask when the rule was
  last decided, not when it was last edited.
- **When a conversion is mechanical, the DIRECTION of the type change has a mirror, and the mirror
  is where the bug is.**
- **SCORE THE RULE YOU SHIPPED, NOT THE RULE YOU DESIGNED.** Any change to a rule after it was
  scored re-opens the score; re-run the measuring script as the LAST act of the build.
- **A RULE THAT READS A CLOCK MUST BE APPLIED ONCE**, and "the writer distrusts its caller" is not a
  reason to apply it twice; carry "already checked" in a TYPE only the checker can mint (X-f1e2, a
  midnight roll between two applications of one floor).
- **AN APPEND-ONLY TABLE NEVER LICENSES AN UNSERIALISED READ-MODIFY-WRITE IN THE SAME TRANSACTION.**
  Name the tables the transaction WRITES, not the one the ruling is about (R-EN, N-190).
- **IDEMPOTENCY IS A PROPERTY OF A REQUEST, NOT OF THE ROW IT CARRIES.** A unique index over the
  row's values must mis-classify a retry or a re-assertion; ask which way, and what it costs (R-EQ).
- **WHEN TWO DOORS WRITE ONE FACT, ALIGNING THEM CAN BE WORSE THAN DELETING ONE.** Ask what each
  door's SURFACE means before making them agree (X-f1e1).
- **AN IMPOSSIBILITY ARGUMENT THAT CONSIDERS ONE LOCK CLASS IS NOT AN IMPOSSIBILITY ARGUMENT.** Name
  the classes it ranges over: the deadlock ruled out advisory-vs-advisory reproduced advisory-vs-ROW
  against a real PostgreSQL (N-193).
- **READING A LAZY RELATIONSHIP IS A FLUSH**, so where a guard SITS decides which exception net
  catches the request's first UPDATE -- a guard moved for READABILITY moved a database write (X-ap).
- **A READ DOOR THAT GAINS A RAISE BREAKS EVERY REPAIR PATH THE UI ADVERTISES THROUGH IT.** When a
  reader starts validating, ask which callers were about to overwrite what it now validates (R7b-1,
  where the advertised remedy became a 500 with every gate green).
  **A refusing read and a TOTAL one are two questions**, so publish the total beside it rather than
  softening it, which would delete the refusal for every other caller (X-au-c3).
- **NAME A WRAPPER'S FIELD SOMETHING THE WRAPPED PRIMITIVE CANNOT ANSWER.** `ObservationDay.day`
  compiled against a raw `date` and returned the day of the MONTH into an SQL bound (X-f1e2).
- **A shared primitive reached through a private import means the package boundary is wrong.**
- **A fail-CLOSED gate is scoped by module identity, so creating a module is how you escape it** --
  and SPLITTING one is creating several, because the allowlist matches a package PREFIX. Name the
  ONE leaf that still needs the exemption (`transfer_service` paid; `status_seam` nearly did).
- **TWO INDEPENDENT FACTS IN ONE REQUEST ARE NOT ALTERNATIVES**, and a door that decides which the
  caller meant drops the other (X-au-c3: a corrected Actual archived nothing, and answered 200).
- **A CONSTRAINT CANNOT HOLD A CONVERSATION.** The door owes the CHECK's rule in words with the
  repair in the message, or the user meets it as "invalid reference" (X-au-c3).
- **FIVE RULES FOR ONE EXTRAPOLATION, EACH MEASURED WRONG (`salary:R14-b`, 2026-09-04).** Past the
  saved calendar a modelled payroll deduction has to come from somewhere, and every rule that
  GUESSES it is exact on the shape its author had in mind. On a window holding no whole year: the
  last payday that PAID something reads a capped deduction's clamped final payment as its rate
  (**10.4x**); a year average over the paydays OBSERVED reads **2.00x** the cap at 13 paydays and
  **15.6x** at 1; refusing outright never overstates but flattens the committed line while the
  what-if overlay beside it stays at the user's figure, so a SMALLER what-if reads as LARGER. One
  cause under all three: a sub-year window has thrown the cap away, since a `$500`-a-payday
  deduction and a `$1,000`-capped one price IDENTICALLY for two paydays. Two more failed on windows
  that DO hold a year -- dividing it by the CADENCE (**+3.846%** on the 9.07% of default windows
  whose year holds 27 biweekly paydays) and grading completeness by a COUNT (a 27-payday year passes
  at 26 observed; **60%** understated). **The remedy was not a sixth rule.** It is `S3`: the engine
  prices every payday of the horizon, and nothing extrapolates.

## The surface a human actually sees

- **A REFUSAL THE SUBMITTING SURFACE CANNOT RENDER IS NOT A REFUSAL, and a SUCCESS that renders the
  pre-submission state is indistinguishable from a no-op.** This app's htmx config leaves 4xx
  non-swapping, so a correct 400 left the form sitting there. Press the button and watch (X-f1c4c).
- **AN AFFORDANCE THAT CANNOT SUCCEED IS DELETED, NOT GIVEN A NICER REFUSAL.** When a refusal is
  reached by an ordinary click, the question is why the click was offered (R-ET).
- **A refusal is only as good as the repair it names, and nobody had pressed the button.**
- **A FRAGMENT'S MOUNT IS PART OF ITS CORRECTNESS, AND A RESPONSE CAN DESTROY ITS OWN MESSAGE.** An
  out-of-band swap into a region that re-fetches on the event the SAME response fires is a race the
  response always loses. Ask what re-renders the target, not just whether it exists (N-199).
- **ONE SURFACE GETTING A RULE RIGHT HIDES THAT THE SHARED PARTIAL NEVER GOT IT.** Loan balances
  were read-only on the cockpit for a year; the partial four other surfaces include was not.
- **A JS visibility defect is invisible to pytest** -- drive it in a browser (X-f2-b: 8,556 green
  over a toast that reached the DOM and never appeared).

## Tests and controls that cannot fail

- **Every guard gets a negative control shown to fire, and a REPAIR for a dead control is itself a
  control needing the same mutation.** A correction carries the defect it corrects, and
  **a CARVE-OUT needs a mutation per DIRECTION**: absent and too-permissive are ONE experiment.
- **A TEST THAT REPLACES A DELETED FEATURE'S TEST MUST BE RUN AGAINST THE REVERT.** Deleting a
  behaviour and its test lowers coverage silently unless the replacement is shown to fail without
  the change (two of X-f1e1's four controls passed on the old code).
- **GRADE THE ONE TOKEN THE FEATURE HANGS ON, OR A MUTATION WILL PROVE YOU DID NOT.** Deleting
  `data-toast-auto-show`, and separately `hx-swap-oob="true"`, each left the whole 7,867-test suite
  green over a permanently invisible acknowledgement (N-199).
- **A RESPONSE-BODY ASSERTION CANNOT SEE WHAT A SECOND REQUEST DOES TO THE PAGE.** Where a
  fragment's correctness is its POSITION, grade the position (N-199).
- **A NARROWING MUST ASSERT WHAT IT LEAVES ALONE, NOT ONLY WHAT IT REMOVES**, and **when a change
  makes a claim elsewhere in its own diff false, that claim was the test that was missing** (X-ap
  subtracted a three-member band and silently retired the ARCHIVE transition).
- **Grading a value object's ids is not grading its FIELDS.** X-f2-c1 mutated `purchased_on` to the
  statement day and left 8,671 tests green.
- **A NEW FIXTURE IS A NEW CONTROL, AND IT CAN BE BORN DEAD.** SQLAlchemy accepts an assignment to a
  field that does not exist.
- **A test whose fixture has no data cannot distinguish two producers.**
- **WHEN A CHANGE MAKES THE CORRECT FIGURE EQUAL THE OLD DEFECT'S FIGURE, THE FIXTURE MUST MOVE.**
  X-f3b made a part-spent envelope `posted + reserved`, which for an under-budget row is exactly the
  estimate F-002 printed, so the `$500` assertion stopped telling them apart (fixture: OVERSPEND).
- **An assertion that cannot fail is not a test**: `hasattr` on a dataclass, `is not None` after
  `isinstance`, and a list returned for its COUNT whose count is never asserted.
- **CONVERTING A SURFACE TO "RAISE" BLINDS EVERY TEST WHOSE FIXTURE CANNOT REACH IT.**
- **A SUITE THAT PASSES ON 353 DAYS A YEAR IS NOT A GATE**, and the day it fails it will look like
  your change.
- **Ask what a test's failure would have COST before deleting it, and write the answer down.**
- **THE STATE A GUARD DEFENDS AND THE STATE THE APP IS IN CAN BE OPPOSITES**, and one written
  against the wrong failure mode can still be a good guard -- write the reason beside it.
- **A skip is safer to state than a fire**, when the operation being guarded is the one under test.
- **A GATE OVER A MAPPING NEEDS BOTH ARMS, OR IT CATCHES A RENAME AND MISSES AN ADDITION**, and its
  docstring will claim otherwise -- read what a gate asserts, not what it says it asserts (R7c-c).
- **AN ASSERTION ABOUT PERSISTED STATE MUST NAME THE STORE THE CODE UNDER TEST WRITES TO.** A
  hard-coded database name beside an app pointed at a copy passes with every refusal ACCEPTED.

## Instruments, oracles and censuses

- **AN INSTRUMENT MUST BE SHOWN TO HAVE REACHED ITS SUBJECT.** One that cannot authenticate reports
  no differences, loudly and wrongly. Assert the identity a result is attributed to, not just that a
  result came back.
- **A HARNESS THAT ADAPTS TO BOTH SIDES CAN GRADE NEITHER'S CONFIGURATION.** `C2-f2d-1`'s ran a pass
  per producer on both, so 37,295 identical lines said nothing about the SHARING that shipped.
- **A GATE MUST BE EXERCISED AGAINST THE ARTIFACT IT GRADES**, never only synthetically: row
  **P52**'s `grep` canary is line-based against a call site that WRAPS, so "the gate passes" has
  always meant "nothing matched".
- **AN ORACLE IS WORTH ONLY ITS INDEPENDENCE FROM WHAT IT GRADES**, and there are three ways to lose
  it: STATING a different rule than the engine, which lets both be wrong together; AGREEING with a
  producer that READS the value being replaced (X-au-b scored 997 of 997 by
  `return txn.estimated_amount`); and MINING the expected series out of the column under test
  (X-au-a backfilled the price history FROM the rows X-au-b's 452 agreements then re-attested).
  **INVARIANCE is the control that sees all three** -- perturb the input the new rule must not be
  reading and require the answer not to move. Ask where the expected value came FROM, not just
  whether it differs from the code under test.
- **A BASELINE IS ONLY A BASELINE AGAINST THE DATABASE IT WAS TAKEN FROM**, and widening an
  instrument is a shape change needing the same normalization the code does.
- **A CENSUS'S METHOD BOUNDS WHAT IT CAN FIND.** Scan with an AST, not a regex; an AST census is a
  grep with better manners unless it FOLLOWS THE DATA; one that greps for a NAME cannot tell code
  from prose; and COUNT THE CALL GRAPH, NOT THE CALL SITES -- one finding said four spellings, the
  tree held 18. A census and a gate can be blind the same way, and then they confirm each other.
- **A CENSUS THAT IS NOT COMMITTED IS AN UNCITED CLAIM.**
- **"IS THIS NAME LIVE?" IS A CLASSIFICATION, AND A REGEX IS A SAMPLER OVER IT.** Six ways one
  question gave a wrong answer in one night: prose on the line; a dict key WRITING the variable for
  a subprocess; an expression spanning two lines; a private constant that REPLACED it and shares its
  spelling; a substring of a different identifier --
  **a name is not a read, and a name is not even always a name** -- and a METHOD NAME attached to a
  number, which suppresses the re-derivation that would have caught it rather than causing the
  error. Seven derivations, five answers, and the only two that held were the two where somebody
  ENUMERATED AND READ. **No figure is recorded here on purpose**: the count moved every time it was
  quoted.
- **A NEGATIVE TEST CANNOT DISTINGUISH *past this change* FROM *before its predecessor*.** Auditing
  which worktrees had adopted a new wrapper by the ABSENCE of a file read as adopted for any tree
  older than the file itself. Census by a string the new thing INTRODUCES, never by what it removes.
- **THE FORMATTER'S CLAIM ABOUT ITSELF IS UNGRADED.** `rumdl fmt` corrupts inline `#NN` refs, and
  "it only re-wraps" is exactly the sentence a tool writes about itself. The procedure is cheap:
  snapshot, fmt, check the corruption signature, confirm a known ref survives BY NAME, then diff the
  whitespace-collapsed WORD STREAM. That is the difference between trusting a formatter and grading
  one, and it ran four times tonight without a single false pass.
- **A PROBE MUST BREAK ON A STATE IT OBSERVED, NEVER ON THE ABSENCE OF ONE.** FIVE instruments in
  one day turned *I failed to measure* into a confident, plausible answer, three of them arriving as
  a GREEN exit: a swallow reporting a fault as a stale image, fixtures reporting a broken container
  as a skip (**N-460**), a mutation harness reporting a mutation it never applied, a PR watcher
  breaking on `!= OPEN` when an API error yields no state at all, and a second one grepping for the
  BUSY string an error message does not contain. A rule about `!= OPEN` catches one and misses the
  other; only OBSERVED catches both, and the three that are not poll loops. Give UNREACHABLE and
  ABSENT separate arms: an API that answers and omits the check is PERMANENT rather than transient,
  and settles green forever the day a job is renamed.
- **A ROUTE SWEEP WITHOUT THE HTMX HEADER CANNOT SEE AN HTMX-ONLY DEFECT, and reads as a clean bill
  of health.** 292 routes returned zero 5xx on a release candidate; the same sweep WITH `HX-Request`
  over 323 returned one. A plain GET of `/analytics/spending` serves the page SHELL and answers
  200 -- the 500 is the FRAGMENT. This app is htmx-first, so every route sweep inherits it, and the
  corrected sweep finding exactly ONE regression is what made it a bound.
- **A COUNT IN A DOCSTRING IS A CLAIM, AND THIS ARC KEEPS WRITING IT WRONG.**
- **BEFORE READING A CLEAN DIFF, CHECK THE FIGURE THE STEP IS ABOUT IS NOT NULL IN IT.** C2-f2c's
  harness was byte-identical over 39,939 lines while `retirement_marker_index` (**P48**) was `None`
  on every one. A clone samples one owner's CHOICES: WRITE the state in and re-capture both sides.
- **PLANT THE FIRING CONTROL AT EVERY WIRING SITE, AND CHECK THE PLANTED DEFECT IS REACHABLE ON THE
  REAL DATA.** C2-f2a's first dropped the axis's FIRST period and moved `$0.00` (they precede the
  latest assertion), so the harness read blind; at the LAST it moved `-$182.29` and `-$190.39`.

## Working this plan

- **A RULING ID IS A CITATION, SO THE RULING SHIPS FIRST.**
- **REVIEW A FROZEN TREE.** Applying one review's fixes while another is running invalidates both,
  and a mutation-planting reviewer needs its own worktree.
- **A STEP WITH MULTIPLE LEAVES, MIGRATIONS AND REVIEWS SPANS SESSIONS.** Stop at the first leaf
  boundary and hand off; the alternative is a mechanical cleanup pass applied with too little care
  at the end of a long session (X-f1c3, 2026-08-04).
- **A TABLE THAT NAMES ITS OWN `HEAD` IS FALSE THE MOMENT IT LANDS**, because the commit that writes
  it moves `HEAD` past the hash it just wrote. Record the last CODE commit and the remote, which are
  stable, and tell the reader to re-measure the rest.
- **A WITHDRAWN HAZARD IS THE SENTENCE NOBODY RE-CHECKS**: it closes the question a stated one keeps
  open. The withdrawer owns the correction -- say what a SHA touches, not what you do not.
- **Documents rot in days here.** This file is the only one allowed to rot, and every edit re-dates
  it.
- **IN FIVE CONSECUTIVE ADVERSARIAL PASSES, THE DEFECTIVE PART WAS A SENTENCE THE FIX WROTE ABOUT
  ITSELF** (`salary:R14-b`, 2026-09-04). Every measurement held; the self-descriptions did not. The
  fourth instance was written WHILE fixing the third, and two more surfaced only because a line
  ceiling forced a docstring to be re-read. A fix's claims about its own correctness are written
  LAST, under post-measurement confidence, and are the one thing its own tests cannot grade. **Grade
  them separately, and never let the pass that fixes them also be the pass that wrote them.** **The
  mechanism, measured again 2026-09-05 (`pay_calendar:C14-d`): the sentence is written by the part
  of the reasoning the fix has not yet updated**, so the wrong answer is the SPECIFIC wrong answer
  the old model produces -- two docstrings predicted `[13, 27, 41]`, which is what the CASH-anchored
  writer that step DELETES emits, against the measured `[13, 28, 42]`. Both were added to satisfy a
  review finding about unauthorised future failures, so the review that caught one ungraded claim
  was the vector for the next.
- **A stated count that CONFLICTS is not a menu.** `bank_import:X-gi-1` merging a registry PR: its
  branch said 334 rulings, `dev` said 336, the merged truth was **337** -- a value on neither side.
  Every other instance here is a counter merging SILENTLY, and those drills end
  *"a conflict is a lucky signal"*; this is the other half, where the marker fires and is itself
  what invites picking a side. The row conflict beside it kept ALL THREE rulings. A marker is a
  prompt to DERIVE.
- **And two sides AGREEING is not evidence either, which is where the instinct hides last.**
  `balance:X-f3c-2b-2c`'s THIRD merge in one night, TWICE in one tree: branch and `dev` both said
  **305 steps** where the merged table held **306**, and both said **132 balance rulings** where it
  held **133**. The conflict arm cannot fire when the sides agree, so agreement carries no signal at
  all while reading as corroboration. Neither side had counted the merged table because neither
  could: it did not exist until the merge made it. **Nor does conflict predict a third value**: of
  nine counters some landed where neither side stood (ledger 302/297 -> **301**) and two landed on a
  side's (ready 75/76 -> **75**). DERIVE every count from its own producer, every time.
- **A pylint score is a RATIO, so widening the denominator hides the message.** `bank_import:X-gi-1`
  graded `app/routes/` at 10.00/10 with a real `line-too-long` in the one file it had edited; that
  file alone scores **9.44/10**. With a hand count of 163 against `crowded_ledger_rows()`'s 160, one
  mistake twice: asking a SUMMARY, or a tool you built, not the producer that owns the answer.
- **A count is not a measurement unless something in the same stream is known to be NON-ZERO**, and
  an absence assertion needs a control that fails if the FEATURE is dead, not if the PAGE is empty.
  `salary:S3-c`'s lane, worktree deleted underneath it, found `grep -c` over a FAILED command
  returns `0` -- indistinguishable from absent. Not the *ask the producer* rule: here you ARE asking
  it and it never ran. `balance:X-bu`'s lane re-ran two claims with a positive control and found it
  had been right *for a reason it had not checked*. `bank_import:X-gi-1` MEASURED the cost by
  killing `?open=`: **8 of 10 no-script cases failed, 2 passed**, both survivors asserting absences
  over `status_code == 200` -- which proves a page rendered, not that the feature lives.
- **A read-only fixture that SELECTs every `ref` table leaves its session idle-in-transaction
  holding a share lock on each of them.** `pay_calendar:C17-a`'s migration replay dropped a key onto
  `ref.pay_cadence_kinds` from a nested `app.app_context()` and waited out `lock_timeout` -- a
  `LockNotAvailable` on a table no test was touching. The holder was `conftest`'s `ref_cache.init`,
  whose reads had never been committed or rolled back. End a fixture's read transaction when its
  reads are done (`tests/conftest.py`, the rollback after `ref_cache.init`); a test that reads a
  fixture object's attribute after a commit re-opens that transaction too.
- **The literal arithmetic of a specification can name a PHANTOM PAYCHECK.**
  `pay_calendar:C17-b-2`'s spec said the record continues at "the first grid payday after the last
  recorded one"; on the catalogue's own thirteen-day shape (01-02, 01-15 at cadence 14) that rule
  ends the 01-15 paycheck on 01-15 and projects a 01-16 paycheck the owner was already paid early.
  The rule was MATCHING (**R-PC72**: a record stands for its nearest planned payday). Walk a worked
  example through the OFF-grid case before building the on-grid arithmetic.
- **A memo keyed by a row's id is a cache of that row's content, and two green branches can each be
  one half of the defect.** R16-b-2 keyed the read pass's rule-resolution memo by `rule.id`; R7d-c-2
  made generation read that memo. Each branch's suite was green; the merge failed two of dev's R17
  controls (a rule re-authored and regenerated on one pass walked the pre-edit cadence), controls
  that belonged to neither lane. The tell was the id key's implicit invariant, "this id means one
  thing for the pass's life", which only a route convention maintained. Remedy: key a memo by the
  derivation's input (the spec), and when a step makes a NEW READER of a memo, run the whole suite
  of the merged tree, not the two branches' modules (`recurrence:R-R73`, 2026-09-11).
- **A ruling can rest on a premise nobody measured, and the premise is the part nobody re-reads.**
  **R-PC70** placed `N-493`'s repair at the era-mint door because "the read path is already LOUD --
  the calendar raises and the recovery page renders"; `pay_calendar:C17-c-2a`'s session drove the
  state (a four-day closed run under a cadence-4 `prior` era) and nothing raised: two nominal
  paydays coalesced onto one cash day, a projected paycheck vanished, the ordinal ran one high. The
  ruling's remedy was built on the premise, so a door would have been offered from a page no owner
  reaches (**R-PC77**). Measure the sentence a ruling's remedy hangs on before ruling it.
- **A two-client test can run BOTH clients as the first user, and every assertion still passes.**
  Until `balance:X-cr` (`fe53fdc5`) the `db` fixture's one app context gave every request one `g`,
  where Flask-Login caches `current_user`, so the first client to log in was the identity every
  later client's request took (`recurrence:R7d-g-3`'s review, 2026-09-14: the second client's first
  request on user A's account answered 200; the non-owner's POST wrote the owner's extra principal).
  No existing IDOR control was wrong, because each requests as the first login or plants the other
  user's row -- but the fixture's own "the two clients are different users" check passed with B
  running as A. A control over WHO acted needs a positive arm that the actor changed
  (`balance:BAL-495`).
