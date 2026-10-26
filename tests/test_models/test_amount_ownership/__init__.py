"""The amount model's ONE constraint: a figure and a source are paired.

Plan step **X-au-c1**, ruling **R-FI**: *a row's amount is either its OWN -- a
human authored the figure, or the money moved -- or it is DERIVED, and a derived
amount is not stored at all.*  ``budget.transactions.estimated_amount`` and
``budget.transfers.amount`` became NULLABLE in migration ``b3f7c2a9d514``, and
what makes that safe rather than merely permissive is
``ck_transactions_amount_ownership`` / ``ck_transfers_amount_ownership``: the
presence of a figure and the presence of an ``amount_source_id`` are exact
complements, so neither column can move without the other saying so.

**Every test here is a FIRING CONTROL, and the distinction matters** (
``docs/plans/verification.md`` standard 4).  No production row is derived as of
this step, so nothing in the app exercises these constraints; a test that merely
asserted the constraint EXISTS would pass against a constraint that admitted
everything.  Each test below writes the state the constraint is supposed to
refuse and asserts the write is refused, by NAME, at the database tier -- which
is the only tier that can see a writer bypassing the ORM.

The shapes under test, and the real writer each one stands for:

* **a figure kept while a source is declared** -- a cutover that stamps the
  declaration and forgets to empty the column, leaving exactly the stale derived
  figure this arc exists to delete;
* **a figure emptied with no source declared** -- the mirror, which would make a
  row unpriceable with nothing recording why;
* **both moved together** -- the legitimate act, which must be ACCEPTED, because
  a constraint that refuses the correct write is worse than none;
* **two pricing links on one row** -- ``ck_transactions_one_pricing_link``,
  which makes a documented convention structural (the balance README states
  ``template_id`` / ``transfer_id`` exclusivity as a CONVENTION with nothing
  enforcing it; ``credit_payback_for_id`` is the third link);
* **deleting a ``ref.amount_sources`` row a derived row names** -- the FK's
  RESTRICT, without which the ref DELETE would silently convert a derived row
  into one claiming to own an amount it does not have;
* **an AD-HOC transfer declaring a source** -- ``ck_transfers_adhoc_owns_amount``,
  which is what keeps ``uq_transfers_adhoc_dedupe`` working now that the column in
  its key can be NULL (PostgreSQL indexes NULLs as DISTINCT, so two ad-hoc
  transfers with no figure would both insert and the double-submit guard would be
  off);
* **the DOWNGRADE guard**, the migration's only non-DDL logic, driven directly
  because the Alembic chain never leaves a NULL figure behind for it to meet.

The model-property half is here too: ``Transaction.effective_amount`` and
``Transfer.effective_amount`` cannot resolve a derived figure -- they are pure
in-memory reads and the SALARY rule needs the owner's whole pay-period set -- so
they REFUSE rather than answering ``None``.  Those arms are unreachable in the
app today, and these tests are what prove they behave as designed when plan
steps X-au-d..X-au-i make them reachable.
"""
