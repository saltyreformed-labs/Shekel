> **ARCHIVED. Historical record only -- this document governs nothing and
> may be out of date.** The live plan is the tracker, `saltyreformed-labs/shekel-plan`; the code as
> committed is the source of truth for what the app does.

# A merchant answer is a standing rule, as built: the X-gd span (2026-08-26)

**One container and its two leaves, condensed out of
`implementation_plan_bank_import.md` under `conventions.md` rule 5** when that
document reached 194 lines against the 180 its cap leaves after headroom.

Rule 5's three conditions hold. **One finding is carried and it is not
closed**: `N-348`, which `X-gb` opened, stays in `ledger.md` under its own
owner. `N-353` was closed by `X-gd-2` and its predicate is re-stated below so
a reader can check the closure without this file. **No live sentence depends
on this one**: the constraints a later leaf must obey stay on the live `X-gd`
entry, which is where `X-ge` and `X-gf` will read them.

## What shipped

| step | commit | what it did |
|---|---|---|
| `X-gd-1` | `395b14f7` | A merchant became a ROW (**R-GR**): `budget.merchants`, account-scoped, both referrers keyed on `(merchant_id, account_id)` |
| `X-gd-2` | `e7b597da` | The RENAME: `budget.merchant_destinations` -> `budget.merchant_rules`, and the `policy` vocabulary -> `rule` |
| `X-gd-2` | `d1910c95` | The rule STORE (**R-GI**, **R-GS**, **R-GT**). Closed **N-353** |
| `X-gd` | `d1910c95` | The container ticks with its last leaf |

## X-gd-1: the merchant is a row

**NOT a slice of `X-gd` as written** -- it delivered none of that step's three
parts. The scope check `_refuse_unknown_merchants` stopped being what made a
stored rule correct and became a SENTENCE for a stale page;
`fk_merchant_rules_merchant_account` is the guard.

Measured before the migration: 378 recorded lines, 62 distinct merchants, 62
distinct case-folded, so no two rows collapsed into one. 29 stored answers,
every one naming a merchant its own account's lines also name.

## X-gd-2: the rule store

**The four answers are the COLUMNS plus one boolean** (**R-GS**). A stored
ref-keyed discriminator was refused because a CHECK cannot reference a `ref`
row's id, and because the boolean survives `X-f6c` collapsing the two
container columns into a template id. `never_a_purchase` asserts the
CONSEQUENTIAL answer, so a row that fails to state it is the harmless *ask me
every time* rather than a bar nobody chose; it is NOT NULL with no default.
`ck_merchant_rules_one_answer` pins it FALSE on both container answers, so
`RuleAnswer.of` and `CreationBars` cannot read one row two ways.

**The migration's backfill was the money half.** Before `e6b2c07d3f19` a
container-less rule MEANT *never a purchase*; after it that shape means *ask me
every time* unless the flag says otherwise. Measured on a clone of the
developer's own database: 29 rules, 16 naming a template, 12 naming a new
envelope, exactly ONE container-less -- `Capital One Credit Card`, whose 9
unexplained in-calendar outflows come to `-$7,412.94`. (R-GA's "9 of the 91...
of the `-$11,336.36`" is an OLDER measurement against a 2026-08-19 production
clone; an adversarial review caught it being quoted under this step's date.)
**All 29 answers were compared row by row across the upgrade and read back
identical.** The downgrade DELETES the *ask me every time* rows rather than
republishing them as bars the owner never set; round-tripped both ways against
the clone with two planted rows, and the one genuine *never* survived.

**Revocation is a RESTATEMENT.** The withdrawal door is gone: the route drops
the screen's *I have not said* before the service sees it, so `RuleSubmission`
requires its answer and `state_rules` has no null-answer arm. The option is
rendered only on a merchant with no rule.

**The consent receipt** (**R-GT**) is `budget.statement_matches.applied_by_rule`.
WHICH rule stays derivable from the matched line, so nothing points at the rule
row -- a foreign key would force a choice none of whose arms is right
(`CASCADE` deletes money records, `SET NULL` claims the owner ticked it,
`RESTRICT` is **N-302**'s dead end). All 221 acts on the developer's dev
database are ticks.

## N-353, closed

`_named_templates` was the one query on user data in this package with NO scope
clause: it selected `TransactionTemplate.id, .name` by id alone, and the NAME
it returns is RENDERED on the control that says where a merchant's spending
goes. Nothing reachable leaked -- the ids arrive from `merchant_rules.template_id`
on rows already scoped, and `fk_merchant_rules_template_account` holds each to
its account -- but that was safety by DERIVATION over an open set of future
callers. It takes `account_id` now.

## What three adversarial reviews changed, before it was pushed

They converged, and what they found was not cosmetic.

* **Two of the step's own tests were TAUTOLOGIES.** The upgrade backfill's
  "container answer left alone" control asserted the ANSWER, and
  `RuleAnswer.of` reads the container columns first -- so a `WHERE`-less
  `UPDATE` would have passed it. It asserts the FLAG now, which is the only
  thing the statement writes. The category clause's scope control staged a
  stranger's rule naming the STRANGER'S own category and asserted this owner's
  was unused, which is true whatever the clause says.
* **The downgrade's DELETE had no container control at all** -- drop three
  terms and it destroys every template and new-envelope rule, with both
  existing cases green. It has one now.
* **`_reject_spending_answer` let *ask me every time* through** for a merchant
  a source files as an account payment, on a stated reason that named the
  withdrawal this step deleted. Restating a stored *never* that way traded the
  standing bar for the one `_bars` records as INTERIM. Only *never a purchase*
  is taken now.
* **The unchanged short-circuit ran before the completeness check**, and an
  incomplete new-envelope answer has the same four columns as *ask me every
  time* -- so clearing the name box against such a row was reported as
  "already answered for" while the identical click on an unanswered merchant
  was refused.
* **The category select was not TOTAL**, and this step is what made the state
  reachable: teaching `category_has_usage` about this table turns a permanent
  delete into an ARCHIVE, and an archived category had no option carrying its
  stored value, so a browser posted the empty one and the door refused a
  merchant the owner never touched, every pass.
* **The template door was the category door's untouched twin.** Hard-deleting
  a template cascaded every rule naming it away, gated only on settled
  transactions -- live on template 19 (`Clothes`), which has a rule and no
  settled history. And MOVING a template between accounts raised an unhandled
  `IntegrityError` (the composite key has no `ON UPDATE`), which this step made
  unrecoverable by removing the withdrawal that used to be the way out. Both
  are refused with their own sentence now.
* **N-353's predicate was false.** Its remedy shipped exactly as written, but
  "the one query in this package with no scope clause" was an over-claim: two
  more render what they select by id alone. The row is closed on its remedy and
  **N-358** carries the census.
* **Four measured claims were wrong**, including a denominator (`91 unexplained
  outflows` / `-$11,336.36`) carried from a 2026-08-19 production clone into a
  2026-08-26 sentence as though it had been re-taken.

## Three things the specification did not name

1. **The stored-answer option had to be made TOTAL.** Removing *I have not
   said* for an answered merchant changed what a select with no selected option
   falls back to: from a withdrawal to a real recurring envelope, so the silent
   outcome became a rule RE-AIMED rather than withdrawn. `_merchant_summary`
   asks the total `RuleView.label_for` whenever the answer is not offerable.
2. **`archive_helpers.category_has_usage` could not see this table**, so a
   category only a rule used read as unused, was permanently deleted, and took
   the rule with it under a flash that said nothing about it. The cascade is
   right; the door calling it unused was not. Developer approved in scope.
   Measured: 12 new-envelope rules name 6 categories, every one also used by a
   template or a transaction -- reachable, unfired.
3. **`_rules.py` passed its 1,000-line ceiling** and split by CONSEQUENCE
   rather than size: `_rules.py` reads, `_stating.py` writes.

## Verified in a browser

Against a clone of the developer's own 29 rules: 30 selects render, exactly ONE
offers *I have not said*, every stored answer round-trips, and revoking
`ATM Withdrawal` from its template to *ask me every time* and back works with
no JS errors.
