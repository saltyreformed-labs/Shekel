> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# `balance:X-au-k` as built: a row's amount ownership is ONE attribute (2026-09-02)

**What shipped.** `budget.transactions.estimated_amount` /
`budget.transfers.amount` and `amount_source_id` stopped being two independently
mapped columns. Both models now map the pair through a SQLAlchemy `composite()`
onto `app/models/amount_ownership.py`'s frozen `AmountOwnership`, under the same
attribute name on both tables. The halves are private columns behind read-only
`hybrid_property` shims, so every read and every class-level query expression is
unchanged and every write spelling raises. Ruling **balance:R-IW**.

**No migration, and it was measured rather than asserted.** The columns keep
their SQL names (`db.Column("estimated_amount", ...)`), so alembic's
`compare_metadata` against a template migrated to `b7a41e2c9d63` reported ZERO
differences on either table -- the six it did report are pre-existing
`system.*` tables that carry no ORM model (`audit_log`,
`pre_origination_purge`, `loan_due_date_backfill`).

## What the step was for, and what it replaced

`app/services/amount_ownership.py` opened by calling itself *"the ONE writer of
a row's amount-ownership pair"*. That was a CENSUS. Any code could write one
column; `ck_transactions_amount_ownership` caught the half-write at FLUSH, after
the unit of work was already built; and keeping callers away from that failure
meant re-counting the write sites every time a cutover grew the derived
population.

**The two sites that made the census unmaintainable are the two a census cannot
see.** `recurrence_engine/_maintain.py` and `routes/transactions/mutations.py`
write `setattr(row, field, value)` over a field name held in a VARIABLE. No grep
and no AST pass can find them. An AST census taken at the start of this step
reported 13 app sites and 401 test sites and did not include either of them.

**And the census MISSED two more, which is the best evidence the step has.** It
matched literal keywords and attribute assignments, so it could not see
`Transaction(**data)` -- and `routes/transactions/create.py` does exactly that,
twice, splatting a schema-loaded payload that carries `estimated_amount`. The
census said "zero app write sites remain"; the first full-suite run then failed
28 tests through those two lines. **Nothing was silently wrong, and that is the
whole point**: a missed site is now an `AttributeError` at the write rather than
a half-written pair at the flush, so the completeness of anybody's census
stopped being load-bearing. The write surfaces were re-censused afterwards over
every shape a keyword pass cannot see -- `Model(**dict)` splats, `setattr` over
a variable, bulk `query.update`, Core `insert`/`update`, raw SQL -- and the last
three do not touch the pair anywhere in `app/`.

## The four shapes, and which tier refuses which

| shape | means | refused by |
|---|---|---|
| figure, no relation | the row OWNS its amount | nothing -- legal |
| no figure, a relation | the row's amount is DERIVED | nothing -- legal |
| figure AND a relation | the stale derived figure R-FI deletes | the TYPE |
| neither | ownership not stated yet | the TYPE, and the CHECK at the INSERT |

`AmountOwnership` is TOTAL over the two legal shapes and refuses both others.
"No ownership stated" is spelled `None` on the attribute, and the mapping is
what makes that work: the composite's `composite_class` is the module-level
`from_columns` factory, not the class, so SQLAlchemy gets `None` for a row
whose two columns are both NULL and never asks the class to represent a state
it has no member for.

**That indirection is load-bearing, and a first version of this step got it
wrong.** SQLAlchemy builds a composite from raw column values inside its own
machinery -- `get_history` for the pre-change side of an attribute that may
never have been set, and `Session.is_modified` on a pending row -- and both
hand it `(None, None)`. Mapping a validating class DIRECTLY therefore raises
from a path no caller entered. The first version did exactly that, and rather
than add the factory it WEAKENED THE TYPE to admit the empty pair, then
documented the weakening as forced by the ORM. **An adversarial review refuted
that in one probe**: `composite()` takes any callable, so a five-line factory
keeps the type total and satisfies SQLAlchemy both ways. The type/database
split was a consequence of one design choice, not a constraint, and saying it
was forced was the kind of claim a fix writes about itself that its own tests
cannot grade. `TestTheFactoryAbsorbsTheEmptyPair` is the regression control
and it FIRES: mapping the class directly fails exactly it and the mapper
assertion, three tests, and nothing else in those two files.

**The database CHECKs STAY**, and after this step they are not redundant: they
still refuse the empty pair a half-built row passes through in memory, and they
are the only tier that sees a writer that is not this application.

## What was rejected, and on what evidence

**A `@validates` that CLEARS the partner column.** Costs zero call sites and is
the project's own idiom (`models/mixins.py`'s settle-instant refusal). Rejected
because it makes a machine splat SILENTLY un-derive a row where today it
raises.

**A `@validates` that REFUSES the illegal pair.** *This option was never put to
the developer at the gate, and that was the review's central charge.* It was
measured afterwards and it does fire through every shape that matters --
`setattr` over a variable name, a `Model(**dict)` splat, a direct assignment --
at zero call sites, and under it **N-437 would not exist**. What it does not
do is deliver what was actually ruled: the pair stays two columns, so the seam
keeps a per-table figure-column registry, and every legitimate state
TRANSITION becomes two statements whose ORDER is load-bearing -- a derived row
re-priced by a human must release before it states, or the validator refuses
the write. It trades a pairing that is a convention for an order that is a
convention. The developer was shown the refutation and the measurement on
2026-09-02 and ruled to keep the composite and repair it.

**Deleting `amount_source_id` as a stored derivation.** The enum's two members
map 1:1 onto the pricing links, which `ck_transactions_one_pricing_link`
already makes exclusive, so the column looks derivable. **One of the two
grounds first given for rejecting it does NOT stand and is struck here.** It
said that because `template_id` is `ondelete="SET NULL"`, a CHECK reading
"derived rows have a pricing link" would refuse a definition delete --
disqualifying. But `budget.transfers` ALREADY ships exactly that constraint
(`ck_transfers_adhoc_owns_amount`), so the behaviour called disqualifying is
behaviour this application already runs on half the tables. The surviving
ground is **N-264** alone: CC4c's projected finance charge is a planned derived
row that carries no pricing link at all. That is a real ground and it is also a
speculation about an unbuilt, unruled step at rank #82, which is why the
question of deleting the column is recorded as its own decision rather than
settled here.

## What the seam lost, and what replaced it

`_FIGURE_COLUMNS` -- the `{model: figure column name}` registry and its
`_figure_column` lookup -- is DELETED. A transaction stores an owned figure in
`estimated_amount` and a transfer in `amount`, so an act had to look up which
column to write; both models expose the pair under the same ATTRIBUTE now, so
there is nothing to dispatch on. The three acts are one line each.

*This was first written as "the measure of the change", crediting the composite
with the deletion. That is not right and the claim is withdrawn: the dispatch
existed only because the two tables' figure columns had different ATTRIBUTE
names, so any design that gave both models a matching private name would delete
it too. What the composite is responsible for is that the shared name is a
PUBLIC attribute a service may write, rather than a protected one the seam
would have to reach past its own lint gate to touch.*

What the registry also bought was a COMPLETENESS predicate, and that was worth
more than the dispatch. It is restated against the MAPPERS in
`tests/test_services/test_amount_ownership_writer.py`: every table whose TABLE
carries an `amount_source_id` column must map an `amount_ownership` composite
over exactly that table's two columns, in that order. **It reads the table's
column names and not `mapper.columns`**, and that distinction is load-bearing --
`mapper.columns` is keyed by ATTRIBUTE, so it answers `_amount_source_id` after
this step and a census written against it goes VACUOUS. It was written that way
first and measured empty.

## How the controls changed, and why that is stronger

`tests/test_models/test_amount_ownership.py` declares every test in it a FIRING
CONTROL and says its builders *"write bare columns on purpose -- a control
routed through a door would grade the door"*. After this step the ORM cannot
express the two unpaired shapes at all, so those controls would have graded the
new TYPE instead of the database. They INSERT through SQLAlchemy Core now,
bypassing the ORM entirely -- which is what the constraint's own comment says it
exists for: a writer that is not this application (a migration, a `psql`
session, a trigger). Mutation-checked: making one Core insert legal fails
exactly that one test.

Three tests in `test_services/test_amount_source.py` grade the INVARIANCE
property -- a derived row's answer must not move when its own stored column
does -- and that property needs a derived row holding a rival figure, which is
now unconstructible through the seam by design. They reach past the mapping to
the private column, under `no_autoflush`, and say so.

## The residual, stated at its real strength

**The private columns are double-underscored, so Python mangles them.** The
single-underscore spelling a reader would guess -- `row._estimated_amount = x`
-- binds a plain instance attribute that reaches no column, so a write that
misses the seam is a no-op the next read exposes rather than a half-written
pair. `composite()` does require its columns to be separately mapped attributes
(mapping them inline maps them PUBLICLY, which is worse), so there is no way to
have no attribute at all; mangling is the strongest seal available.

*The claim that `composite()` raises `UnmappedColumnError` on inline columns
was wrong and is struck. Inline columns configure fine and are mapped public;
`UnmappedColumnError` comes from `exclude_properties`, a different
construction. The conclusion -- no seal is available -- survives; the cited
mechanism did not.*

**What is left, and it is a census.** A bulk `update()` naming the column
either by the class-level hybrid or by a plain string key compiles to a real
`UPDATE`, through neither the seam nor the type. No code in `app/` does this --
`query.update` appears at 9 sites, none touching the pair, and there is no Core
`insert`/`update` or raw SQL on either column -- but that is a census, which is
the thing this step exists to stop relying on. It is the one write surface the
mapping does not reach, and it is stated here rather than left for a reader to
discover.

The three tests that must construct a state the mapping refuses go through
`tests/_test_helpers.write_past_the_amount_seam`, which resolves the mangled
name from the mapper and RAISES if the model no longer maps it -- because under
mangling a hand-written private assignment would otherwise no-op silently, which
is the right failure for a typo in application code and the wrong one for a
control that means to write.

## Findings

**Closed: N-293.** Its predicate -- the maintain pass writing one column of the
pair without the other -- has no expression left.

**Opened: N-437**, a CONSEQUENCE rather than a discovery, and **it is four
sites wide, not one.** Wherever a writer used to set the figure column alone,
it now states the row's whole ownership; where that used to abort with a loud
`IntegrityError` on a derived row, it can now state `own` over one in silence.
The sites are `recurrence_engine/_maintain.py`'s splat,
`recurrence_engine/_conflicts.py`, `transaction_service/_settle.py` and
`entry_credit_workflow.py`. Each carries a comment saying so.

**Its containment argument was WRONG as first written, in two ways.** It said
the reason is one "the DATABASE holds rather than a census".
`ck_transactions_one_pricing_link` says nothing about `amount_source_id`, so
the database supplies only half of it; the other half -- that today's only
derived rows are transfer shadows -- is a census. And it named `X-au-e` as the
step that closes the window when the step that OPENS it is **`X-au-d`**, which
derives 51 salary rows that are template-linked and therefore reachable by the
maintain pass.

**One of the four sites was a money defect and is FIXED here rather than
recorded.** `_freshest_amount` gated its write on
`live is None or live == txn.estimated_amount`. After `X-au-d` a derived
salary row holds `estimated_amount IS NULL` for exactly the rows
`live_projected_net` answers for, so `live == None` is false for every one of
them and the predicate goes VACUOUSLY PERMISSIVE -- every settle would hand a
derived row back to OWN. That is finding **N-436**'s shape, and before this
step the consequence was a loud `IntegrityError` that `X-au-d`'s own first test
run would have caught. The remedy is a third conjunct, `owns_its_amount(txn)`,
which completes the predicate rather than guarding it: nothing supersedes a
cache that does not exist.
