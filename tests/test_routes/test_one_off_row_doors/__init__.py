"""
Shekel Budget App -- the grid's doors on a RULE-LESS definition's row

Plan step ``balance:X-bi-7a`` (ruling **R-BAL20**, every plan item has exactly
one definition).  Five "no cadence" readings of ``template_id IS NULL`` on
the transaction doors were re-keyed onto ``Transaction.recurs``, so a row
whose definition has NO rule -- template-linked, no cadence: a cleared cadence
today, every one-off once the family's cutover mints it a definition --
behaves as the one-off it is.  No production row changes behaviour (no
rule-less transaction definition exists on the 2026-09-12 restore), so each
case here MAKES one, the way the edit door does: the engine's row under a
cadence, then the cadence cleared (dis-associate; delete-orphan).

Pinned per door, each beside its recurring control so the two arms are
graded against each other and not against a shape the app never writes:

* DELETE -- hard, and the dialog says so (``preview.soft``, the service's
  own arm); **and since plan step ``balance:X-bi-7b`` the definition goes
  with its LAST row** (rulings R-BAL23 / R-BAL27), stays while it holds
  another, and the delete is REFUSED while a standing merchant rule names
  the definition -- the sentence on the card and the door's 400 are one
  rule (``deletion_refusal``);
* due date -- the popover offers the input, a MOVE lands, a CLEAR is refused
  with a sentence that names the rule rather than "invalid reference";
* period move -- ``is_override`` is NOT flipped (developer 2026-09-13: the
  flip's MOVE half keys on ``recurs``), **and since leaf 7b-2 the move
  RE-PLACES the row** (ruling **R-BAL33**): the target paycheck's start
  unless the owner had stated a day, ``occurs_on`` following (R-BAL25);
* typed figure -- **restated on the DEFINITION, in place** (rulings
  **R-BAL21** / **R-BAL29**, leaf 7b-2), the row never detached; a row the
  interim between the two leaves DID detach is re-attached by the same act
  (**R-BAL37**).  Until 7b-2 the flag flipped and the figure landed OWN.
* the ITEM -- name, category and both flags render on the card for a
  placed row and land on its definition (rulings **R-BAL23** / **R-BAL36**);
  a recurring row's crafted flag is dropped by the schema (BAL-484's writer
  gone); *make this repeat* opens the definition's edit form; the dialog
  says the item goes with its only row.

**Two shapes of rule-less row are graded**, and which each case uses is
deliberate: ``_one_off`` is the pre-7b-1 shape (a cleared cadence, whose
series can hold several versions -- what the restate's walk is graded on),
``_placed`` is the shape the app writes today, through the one producer.
"""
