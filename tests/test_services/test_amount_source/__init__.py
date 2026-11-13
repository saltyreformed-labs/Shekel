"""
Shekel Budget App -- Where one row's amount comes from (plan step X-au-b)

The total dispatch of :mod:`app.services.cash_ledger._amount_source`: ruling
**R-FI**'s amount rules (five, four since plan step balance:X-bi-6-5), the
order that makes two of them subsets of two
others rather than rivals, and the refusal each one raises where its producer
cannot answer.

**A DERIVED fixture here carries no stored figure at all, and an OWN fixture
carries one no rule may answer.**  Neither is decoration.  An adversarial review
mutated the resolver to ``return txn.estimated_amount`` -- deleting the dispatch,
all five rules and every refusal -- and the exhaustive production oracle beside
this file reported *997 rows, 0 mismatches, OK*, because for 946 of those rows
the app's own answer IS that column.  A fixture whose stored figure equals its
derived one cannot tell the two implementations apart, so none here does.  Since
plan step X-au-c2 a derived row cannot even hold a rival figure:
``declare_derived`` empties the column as it stamps the source, because
``ck_transactions_amount_ownership`` pairs the two, so the same mutation now
returns ``None`` where a ``Decimal`` is asserted.  The OWN fixtures still carry
``_NOT_AN_ANSWER`` -- for them the column IS the answer, and the figure names
which of three candidates a test means.

**Which rule prices a row is now a question to the COLUMN** (finding **N-262**,
closed here): ``amount_rule`` reads ``amount_source_id`` and no longer infers
ownership from ``is_override`` or from having left Projected.  So every derived
fixture DECLARES its relation, and the three arms that used to be inferences --
overridden, non-Projected, soft-deleted -- are graded in
:class:`TestTheDeclarationDecides` as the inversions they became: the flag and
the status no longer move the rule in either direction.

**The refusals are why most of this file exists.**  Zero rows on the 2026-08-12
production clone take any refusal arm, so production evidence cannot show that a
single one of them fires.  Every guard therefore carries its own control, built
to reach the arm and shown to raise, per ``docs/plans/verification.md``
standard 4.

The LOAN_PAYMENT rule is graded ONLY here: ``budget.loan_payment_settings`` is
empty on production, so the oracle prices ``$0.00`` through it and a seeded loan
is the only place it runs.

**No transfer shadow is born since plan step balance:X-bi-6-4d-2**, and
none can be stored: ``ck_transactions_names_no_transfer`` refused one (ruling
**R-BAL258**) until plan step balance:X-bi-6-5 dropped the column a shadow
named its transfer by.  So a case that priced a shadow asks the TRANSFER, or
prices its legs through ``amounts_by_key`` -- each leg worth
``resolve_transfer_amount`` on its parent (**R-BAL13**), which is what rule 5
answered for the shadow -- with its figures unchanged (ruling **R-BAL167**
class 1).  Rule 5 itself -- a twin's dispatch to its parent and its refusal of
an orphan -- was graded on a twin built UNSAVED (class 4), and three cases
asserted the database refused a planted twin (class 2), until plan step
balance:X-bi-6-5 deleted the rule, its relation and the column: those six
cases went with them (ruling plan#1850), and that step's commit names each.
"""
