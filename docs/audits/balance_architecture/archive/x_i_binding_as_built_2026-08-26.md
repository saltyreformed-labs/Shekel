> **ARCHIVED. Historical record only -- this document governs nothing and
> may never be read as a live plan or as a statement of the current state.**
> The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as committed is the source of
> truth for what the app does.

# The one-read-pass BOUNDARY and BINDING, as built

Cite this for how these two steps came to be, never for what is true now. The code as committed is
the source of truth for what the app does, and the live plan is
`docs/audits/balance_architecture/README.md`. Written 2026-08-26 under `conventions.md` rule 5,
because the live document had no headroom and its record of what is DONE is what shrinks.

Both steps are leaves of `balance:X-i`, whose remaining leaves (`X-i1`, `X-i2`, `X-i3-b`, `X-i5`,
`X-i6`) are LIVE and specified there. Every obligation these two leave a live step is restated at
that step; nothing below is load-bearing.

## `balance:X-i3-a` -- the boundary (`765daebd`)

A request is a QUERY or a COMMAND and its transaction says which (ruling **R-GU**). GET and HEAD run
at `REPEATABLE READ, READ ONLY`, so a render is ONE snapshot and cannot write; every other method
keeps `READ COMMITTED`, because the reconciles' lock-then-reread depends on it. Bound at the
session's `after_begin` rather than in a before-request hook, which is how the audit actor had been
lost and was recovered here. `write_transaction()` is the one door a render has when it must write,
and the GET handlers that wrote either declared it or were deleted.

Measured before the ruling: the mechanism proven against this application's own session (two counts
across one concurrent committed append answer `(1, 2)` today and `(1, 1)` under the query mode),
2,302 of 2,978 suite GETs arriving on an inherited transaction, and 36 arriving on one that had
already written.

Closed **N-353**; opened **N-358** and **N-359**.

### Corrected at `4dddfe73` -- WHERE the decision is taken

CI on PR #134 reported this as a cost: `test_no_baseline_policy`'s `url_map` sweep blew its 30 s
budget at 25.38 s, and a per-statement tally put the step at **+2,954 statements over 444
requests**, a 52% increase. **What it was is a MEASURED COST and a LATENT correctness hazard**, and
the second half is stated that way because an adversarial review refuted the stronger claim this
paragraph first made.

`765daebd` decided the mode in a `before_request` hook, and Flask calls those in REGISTRATION
order: `setup_logging`'s `_attach_request_id` is registered first and reads `current_user`. So the
request's first statement -- and the transaction carrying it -- ran at `READ COMMITTED` before any
mode existed; the boundary then threw that transaction away, and the render's real snapshot was its
SECOND.

**The refuted claim was "everything the first one read was outside the snapshot the page is
computed against".** Measured, tagged by transaction, on one authenticated `GET /settings` at
`765daebd`: the discarded transaction held THREE statements -- the `auth.users` load, one actor
bind, and the refusal probe -- and the user row is re-read inside the render's own snapshot
anyway, because the rollback expires it. So no figure any page published was ever computed against
it. What it did carry out was `load_user`'s authentication decision (`is_active`,
`session_invalidated_at`, the idle check), read on a snapshot the request then discarded: not
money, and a window measured in statements.

**What makes it a hazard rather than a curiosity is that WHICH hook is first is not a property
anything holds.** The rule was one before-request registration away from a money read landing
outside the render's snapshot, and nothing in the module, the gate or the suite would have said so
-- which is **N-353**'s own shape, at one remove.

Two of the module's own claims were false with it: that the boundary was a TEST-FIDELITY property,
and that its opening hook "is a no-op on every request" in production. Both were untrue for the
same reason, so a discarded transaction, a refusal probe and a doubled user load were on every
production GET.

The decision moved to `request_started`, which `Flask.full_dispatch_request` sends immediately
before `preprocess_request` (`flask/app.py:914-915`), so it precedes every before-request hook by
Flask's own dispatch order rather than by an order this module polices. The receiver is a
module-level function because blinker holds receivers WEAKLY: a closure defined inside
`register_transaction_boundary` would be collected when that call returned and the signal would
silently stop firing.

**And a query stopped being told who is ACTING.** `READ ONLY` refuses every write to an audited
table, so no trigger in a query's transaction can fire and nothing can read `app.current_user_id`
-- three `set_config` round trips per authenticated GET with no reader. The actor binds through one
door now (`db_transaction.bind_request_actor`), which also removed one key with two spellings in
two modules: `logging_config` wrote `g.shekel_audit_actor` as a literal while `db_transaction` read
it through `_ACTOR_KEY`. The teardown retires the actor with the mode, because under the shared app
context of the test client an actor left on `g` signed every row the test body went on to write --
which neither production nor the `SET LOCAL` this replaced ever did.

The `+889 SELECT auth.users` the cost brief could not account for:
`flask_login/mixins.py`'s `UserMixin.is_authenticated` returns `self.is_active`, and
`app/models/user.py:100` makes `is_active` a real column, so reading it on an instance the
boundary's rollback expired issues a SELECT -- twice per request, with the rollback between the two
hooks that read it.

Measured. One authenticated `GET /settings`, statement by statement: **15 statements to 10**, the
boundary and actor machinery within it **7 to 2**. Over the sweep file at `-n 0`: **8,624 to 6,331**
against `dev`'s 5,670, so **78%** of the regression is removed; the 549 remaining `SET TRANSACTION`
are one per query transaction and are the guarantee itself. Wall clock under this project's
CI-shaped harness (pytest pinned to 4 cores with `taskset` at `-n 12` over `tests/test_routes/`,
the method `pytest.ini` records), for the arm that timed out: **1.18 s** on `dev`, **3.24 s** at
`765daebd`, **2.11 s** here -- and `pytest.ini` independently records 1.17 s for that arm on `dev`
under the same harness, which is what makes the reading trustworthy.

Four controls, each shown FAILING at `c92a8313`. The load-bearing one asserts EVERY transaction of
a GET is one snapshot rather than that some transaction was read-only -- on `765daebd` a
`GET /settings` observes `[('read committed','off'), ('repeatable read','on')]`. It needed a
`db.session.rollback()` as SETUP to reproduce a production request's starting state; without it the
control passed against the defect it exists to catch, found by running it both ways.

The residual is **N-364**, which is the sweep's own shape and not this step's.

## `balance:X-i3-b` -- the accommodations (`1feb0930`)

The three live accommodations for "under `READ COMMITTED` the two reads can differ" narrowed to the
COMMAND each still describes. None deleted: `_candidates`' period-id scope is ALSO the ownership
scope, and `_scope.period_holding` pairs two different REQUESTS (a line is OFFERED by the GET and
PLACED by a POST), which no per-request snapshot could reconcile.

The census was re-taken and found more than the specification named. Eleven statements carrying the
literal phrase, across nine `app/` modules plus one script -- which is the "nine sites"
`db_transaction`'s own docstring counts, and the count is of MODULES rather than statements. A
TENTH `app/` module describes the same class without the phrase (`exceptions.RecurrenceWindowError`,
narrating the check `pay_calendar:C2-f3c` deleted), so a grep for the phrase is a lower bound on
this class and is recorded here as one. Among the eleven were three query-reachable sites the step
never listed
(`_scope`'s `ReviewScope.calendar` and `period_holding`, `_reads.awaiting_review_count`) and one
claim `X-i3-a` had FALSIFIED -- `anchor_service`'s lock-then-reread justified itself with "READ
COMMITTED, verified as the default on dev, test and production, with no override anywhere", and
there is an override now. It rests on something stronger than a census: the function WRITES, and
the override applies only to transactions PostgreSQL would refuse a write in.

Request-kind reachability was measured rather than assumed, and the FIRST measurement of it was
wrong. `ReviewScope.build` has five call sites, and counting sites rather than FUNCTIONS gave "one
GET and four POSTs" -- which named `release_statement_match`, a door that builds no scope at all.
An `ast` walk over the module gives the real answer: **one GET door and THREE POST doors**, with
`apply_statement_review` building TWO, deliberately -- a fresh scope for the ANSWER on the path that
wrote, because the pass it was applied against describes a state that no longer exists. The
docstrings that had carried the wrong count were corrected before this shipped. The lesson is the
project's own: a census over line numbers is not a census over the things being counted.

`awaiting_review_count` is the one accommodation that goes fully dead -- its only caller is
`grid/page._bank_control`, whose only caller is the `/grid` GET -- and its DRY reason survives
untouched. **A first draft of its docstring said "a QUERY, whose whole request is one snapshot",
and `/grid` is the one route in the application for which that is false**: it opens a
`write_transaction` block for the rolling top-up, so it runs read-only, then writable, then
read-only over a NEW snapshot, which the step's own `test_a_grid_render_is_query_command_query`
asserts. What holds is positional -- the block is the first statement of `index()` and both reads
fall well after it -- and the docstring says that instead. An adversarial review found it.

Opened **N-364** and **N-365**.

## `balance:X-i4` -- the binding (`79a1730c`)

A read pass BINDS the account it values (ruling **R-GV**). `_context._memoize_once` -- the one
primitive that creates `account.id`-keyed state on a `BalanceContext` -- takes the `Account` and
refuses one whose owner is not the pass's, BEFORE the membership test, so a warm cache cannot serve
a foreign hit. All five funnels inherit it and nine public seam entries inherit it transitively with
no check of their own; `loan_walk` stopped open-coding a fourth copy of the store-once lines.

`_cash_fold.assemble` and its four siblings went as a mis-pairable shape: one door
(`assembled_fold`) and four readings that take the assembled record and carry no account and no
clock. `_cash_periods.cash_period_view`, which had no `app/` caller at all, was deleted rather than
re-signatured.

**What a mis-pairing would have published**, measured on the parent commit: a first owner's pass
handed the second owner's Checking answered `$2,000.00` -- that owner's real asserted balance --
across all ten of the FIRST owner's period columns. The transactions are scenario-scoped so none of
them folded; `cash_ledger.cash_anchor_facts(account_id)` takes no scenario and no owner, so the
assertions replayed.

**The memo**, measured on the dev database over the six cash entries a single-account screen asks:
42 `walk_cash_ledger` calls across 8 accounts before, 8 after. `records_balance_at` alone walked one
account TWICE inside a single call -- once for its assertion lookup, once through its
`cash_balance_at` fall-through.

**Two bindings the adversarial reviews forced**, each finding **N-354**'s own sentence one field
over on the same object: `BalanceContext.__post_init__` refuses a scenario belonging to another
owner (measured answering a real figure), and `AssembledCashFold.account_id` binds the record
`_asset_fold.resolve` folds an account onto -- that reader memoizes nothing, so the pass's refusal
never reached it, and the step's first build had edited its docstring to claim otherwise.

**The first build made the fold's cache PUBLIC** and both reviews measured the same bypass
independently: an `AssembledCashFold` carries `seed` and `steps`, so a prefix sum over it reproduces
the seam's own scalar, and a consumer importing nothing private could read a balance the W9910 fence
exists to make unreachable. W9910 sees imports and `protected-access` sees underscores, so neither
saw a public dataclass field; W9909, which does scope public methods on that class, refused the
public method form outright. The cache is `_cash_folds` now, filled through one named
`protected-access` crossing in `_cash_fold.assembled_fold`.

**Four tests** reused one pass across a write and were corrected to build a fresh one -- the
contract `BalanceContext` already documented, and what every write path in `app/` already does. No
asserted value changed. The fourth was found by a session-level write probe after the first three.

Closed **N-354**; opened **N-360**, **N-361**, **N-362** and **N-363**.
