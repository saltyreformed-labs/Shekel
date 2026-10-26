"""
Shekel Budget App -- Cash ledger: WHICH RULE prices one row.

**The DECLARATION, and the refinement that picks between the rules it admits**
-- the half of ruling **R-FI**'s dispatch that classifies, split from the half
that answers (:mod:`._amount_source`) at plan step **X-au-f-2**, which took
that module past ``max-module-lines``.  The cut is by tier and it is the seam
the sibling's docstring already draws: *ownership is DECLARED and the
refinement is READ, and the split between the two is the design*.  Nothing here
resolves a figure, reads a price series or touches a live producer; it answers
which of the five rules owns a row's amount and nothing else.

**The alternative was shaving prose, and this project has already ruled against
it**: *"three lines of headroom is not a design, and the structural answer is a
package with one private leaf per verb"* (``transaction_service`` package
docstring), restated by :mod:`._amount_basis` when the same ceiling forced the
same choice at plan step X-au-j.

**TWO ROW TABLES, two classifiers, and neither takes all five rules.**
:func:`amount_rule` classifies a ``budget.transactions`` row and can answer
rules 1, 2, 3 and 5; :func:`transfer_amount_rule` classifies a
``budget.transfers`` row and can answer rules 1, 3 and 4.  Rule 2 needs a
salary profile, which names a TRANSACTION template; rule 5 is *= my parent*,
and a transfer has no parent transfer.  Rule 4 belongs to the TRANSFER
classifier since ruling **R-BAL10** -- it was a shadow rule until plan step
X-au-f-2 -- which is what makes rule 5 exceptionless.  No door writes a row
rule 5 can answer since plan step ``balance:X-bi-6-4d-2`` deleted the shadows
(ruling **R-BAL166**): a stored row that still declares the relation is
classified here and refused one tier down, having no transfer to read
(:func:`._amount_source._transfer_answer`), and that arm goes at plan step
``balance:X-bi-6-5``.

Nothing here imports from :mod:`._amount_source`, so the split introduces no
cycle: this module is strictly below it.  Boundary discipline (``CLAUDE.md``
Architecture / B6-01): ORM rows in, an :class:`AmountRule` out; no Flask
import, no writes, no query of its own.
"""

from enum import Enum

from app import ref_cache
from app.enums import AmountSourceEnum
from app.exceptions import AmountUnresolvable
from app.services import template_amount_service


class AmountRule(Enum):
    """Which of ruling R-FI's five sources a row's amount comes from.

    The dispatch key.  An explicit enum rather than a pair of link tests, because
    the rules are not a partition over the links -- see :mod:`._amount_source`'s
    docstring for the two subset relations (rules 2 and 4 are each a subset of
    rule 3) that make the refinement order load-bearing; they stayed there when
    this module was split from it.

    **It is NOT what the ``amount_source_id`` column stores, and a first draft of
    this docstring said it was.**  That column names the RELATION that prices a
    row -- its definition, or its parent transfer
    (:class:`app.enums.AmountSourceEnum`) -- and the refinement between SALARY
    and TEMPLATE for a transaction, or between LOAN_PAYMENT and TEMPLATE for a
    transfer, is a property of the DEFINITION rather than of the row, resolved
    live here.  Storing the rule
    would put a definition-level fact on every generated row, where two live
    routes falsify it (ruling **R-FK**, plan step X-au-c1).

    **The OWN member is the one that IS the column** (finding **N-262**, closed
    at plan step X-au-c2): a row owns its amount exactly when it carries no
    source, which is the same NULL-ness ``ck_transactions_amount_ownership``
    pairs with carrying a figure.  Before that leaf this member was inferred from
    ``is_override`` and from having left Projected -- states the CHECK cannot see
    -- so the schema and this dispatch could disagree about the same row.  The
    status gate now sits ABOVE the resolver rather than inside it: an excluded row
    is worth ``$0.00`` whatever prices it, and asking a Projected-only producer
    about a Cancelled row is how that used to become a refusal
    (:func:`app.services.cash_ledger.contributed_amount`).
    """

    OWN = "own"
    SALARY = "salary"
    TEMPLATE = "template"
    LOAN_PAYMENT = "loan_payment"
    TRANSFER = "transfer"



def amount_rule(txn) -> AmountRule:
    """Return which of R-FI's five rules owns *txn*'s amount.

    **One question to the COLUMN, then one to the DEFINITION.**  A row that
    carries no ``amount_source_id`` owns its figure and is priced by rule 1; a
    row that carries one names the RELATION that prices it, and the refinement
    inside a definition -- SALARY or TEMPLATE -- is read live off the
    definition itself.  The refinement order is the rule: SALARY is tested
    before TEMPLATE because a salary profile names an ordinary transaction
    template, and testing them the other way round would place every paycheck
    as a template row.  The parent-transfer relation refines into rule 5 alone
    (:func:`_rule_is_parent_transfer`): a loan payment's rule 4 is the TRANSFER
    classifier's since ruling **R-BAL10** (:func:`transfer_amount_rule`).

    **Nothing here reads ``is_override``, ``is_projected`` or ``is_deleted``, and
    that is finding N-262's fix** (plan step X-au-c2).  Those three are facts
    about whether a row COUNTS and about who last touched it, not about who owns
    its figure, and inferring ownership from them let four live doors write a row
    ``ck_transactions_amount_ownership`` admits and this dispatch refused -- the
    module docstring names all four.  What replaced them is the one statement of
    ownership the model has.  Two consequences worth stating because they used to
    be arms:

    * a row a human RE-PRICED owns its figure because the write door CLEARS its
      source and stores the typed amount, not because ``is_override`` is set --
      so the flag can go on carrying its other three facts (finding **N-238**,
      plan step X-au-h) without touching pricing;
    * a SETTLED row is priced by this dispatch like any other, because plan step
      X-au-c3 writes NO plan column at a settle -- what moved is recorded beside
      the plan, not into it.  No money reader asks this about a settled row:
      ``row_valuation.fixed_contribution`` answers from the record first, and the
      dispatch is reached only for a row whose money has not moved.

    **Soft deletion does not change the answer, deliberately.**  Being deleted is
    a statement about whether the row counts, and making it flip the rule would
    force ``amount_source_id`` to be REWRITTEN on every delete and restore -- a
    derived column beside a second writer, the shape this arc exists to remove.
    A deleted derived row resolves like any other and contributes nothing either
    way; the backfill's refusal to MINE a deleted row (migration
    ``a9d3c15e7f42``) is a question about evidence, not about ownership.

    Args:
        txn: The :class:`~app.models.transaction.Transaction` to classify.  Its
            ``template`` relationship is read only when it DECLARES that
            relation, and its ``transfer`` relationship never is (the
            parent-transfer refiner reads nothing), so an undeclared row costs
            no lazy load at all.

    Returns:
        The :class:`AmountRule` that prices this row.

    Raises:
        KeyError: When the row names a relation this dispatch has no rule for.
            Unreachable through the FK, which admits only the seeded
            :class:`~app.enums.AmountSourceEnum` members; it is how a member
            ADDED without a rule beside it fails loudly instead of falling
            through to whichever branch happened to be last.
    """
    if txn.amount_source_id is None:
        return AmountRule.OWN
    return _RELATION_RULES[_declared_relation(txn.amount_source_id)](txn)


def _declared_relation(source_id: int) -> AmountSourceEnum:
    """Return the :class:`~app.enums.AmountSourceEnum` member *source_id* names.

    The id-to-member direction ``ref_cache`` does not publish, because every
    other consumer of a ref table compares a stored id against a cached one and
    needs no reverse map.  This dispatch is the exception: it branches on WHICH
    relation a row declared, so it must turn the stored id back into the member
    the rules are written against.  Derived from ``ref_cache.amount_source_id``
    rather than from a second query, so the two directions cannot disagree.

    Args:
        source_id: A row's stored ``amount_source_id`` (never ``None`` -- the
            caller has already tested for the OWN state).

    Returns:
        The member that id names.

    Raises:
        KeyError: When no member maps to *source_id*.  The FK to
            ``ref.amount_sources`` makes that unreachable for a seeded database.
    """
    return {
        ref_cache.amount_source_id(member): member
        for member in AmountSourceEnum
    }[source_id]


def _rule_within_definition(txn) -> AmountRule:
    """Refine the TEMPLATE relation into rule 2 or rule 3.

    A definition prices its rows either through a salary profile that names it
    or through its own effective-dated series, and which of the two is a fact
    about the DEFINITION read at this moment -- archiving the profile is what
    moves a template from the first to the second.

    ``template is None`` beside a declared relation is TEMPLATE, and that answer
    REFUSES one tier down (:func:`_stated_amount`).  The guard is an ORM
    state, not a stored one: a row whose definition was deleted in this
    session before the flush still WAS generated by one, and asking the
    salary predicate about ``None`` would raise ``AttributeError`` -- an
    unhandled crash where every other unanswerable shape here raises the arc's
    own refusal.  Found by an adversarial review at plan step X-au-b.  Since
    plan step ``balance:X-bi-7d-2`` the database cannot STORE the pairing
    (``fk_transactions_template_id`` is ON DELETE RESTRICT), so the flush
    that would have nulled the link is refused instead; the in-session
    window before it is what this guard still covers.

    Args:
        txn: A row declaring :attr:`~app.enums.AmountSourceEnum.TEMPLATE`.

    Returns:
        :attr:`AmountRule.SALARY` or :attr:`AmountRule.TEMPLATE`.
    """
    return (
        AmountRule.SALARY
        if txn.template is not None
        and template_amount_service.is_salary_linked_template(txn.template)
        else AmountRule.TEMPLATE
    )


def _rule_is_parent_transfer(_txn) -> AmountRule:
    """Refine the PARENT_TRANSFER relation: rule 5, unconditionally.

    **It USED to refine into rule 4 or rule 5, and ruling R-BAL10 is what made
    it total** (plan step X-au-f-2).  It read
    ``AmountRule.LOAN_PAYMENT if _is_loan_payment(txn.transfer) else
    AmountRule.TRANSFER`` -- so a loan payment's two legs were priced by the
    LOAN while every other shadow was priced by its parent.  A loan payment's
    occurrence was ONE economic event held as three rows, its legs were the
    parent's PROJECTION, and the value's home was therefore the parent: rule 4
    became a TRANSFER rule (:func:`_rule_within_transfer_definition`) and a
    shadow's answer became *= my parent* whatever its parent was.  That was
    R-JM's one chain, and what it deleted is the state where a leg answered a
    figure its parent did not -- the ``$174.10`` an owner's typed amount lost
    to the contract, twice.  The occurrence is held as ONE row since plan step
    ``balance:X-bi-6-4d-2`` deleted the shadows (ruling **R-BAL166**), so no
    door writes a row declaring this relation: a stored row that still does
    names no transfer (``ck_transactions_names_no_transfer``) and is answered
    by the refusal in :func:`._amount_source._transfer_answer`.  Plan step
    ``balance:X-bi-6-5`` deletes the relation, and this refiner with it.

    Kept as a one-line refiner rather than folded into ``_RELATION_RULES`` as a
    constant, because that table's entries are CALLABLES and a member added to
    :class:`~app.enums.AmountSourceEnum` must arrive with one; a table holding
    a mix of the two would be read as though the constant were the shape.

    Its parameter is UNREAD and named ``_txn`` to say so: the relation names
    the rule on its own now.  The signature is the table's, not this answer's.

    Returns:
        :attr:`AmountRule.TRANSFER`.
    """
    return AmountRule.TRANSFER


def _rule_within_transfer_definition(xfer) -> AmountRule:
    """Refine a TRANSFER's TEMPLATE relation into rule 4 or rule 3.

    The transfer twin of :func:`_rule_within_definition`, and the same shape:
    a definition prices its rows either through a live producer that answers
    for it -- a salary profile there, a LOAN here -- or through its own
    effective-dated series, and which of the two is a fact about the DEFINITION
    read at this moment.  Ruling **R-FK** is why it stays a live read:
    ``routes/loan/payment_transfer.track_payment`` flips a payment into
    derive mode in one click, so a stored rule would name a producer that no
    longer answers.

    **Rule 4 is reached from HERE and no longer from a shadow** (ruling
    **R-BAL10**, plan step X-au-f-2).  Both of its modes are transfer-level
    answers: derive mode is the contract's cash on the installment's own due
    date, manual mode the definition's dated series price, and BOTH carry the
    standing ``extra_principal`` because it is part of the cash that leaves the
    bank.  A payment in EITHER mode takes rule 4 -- the mode picks the arm, not
    the rule -- which is why the test is :func:`_is_loan_payment` (a settings
    row exists) rather than ``derive_from_loan``.

    Args:
        xfer: A :class:`~app.models.transfer.Transfer` declaring
            :attr:`~app.enums.AmountSourceEnum.TEMPLATE`.

    Returns:
        :attr:`AmountRule.LOAN_PAYMENT` or :attr:`AmountRule.TEMPLATE`.
    """
    return (
        AmountRule.LOAN_PAYMENT if _is_loan_payment(xfer)
        else AmountRule.TEMPLATE
    )


def transfer_amount_rule(xfer) -> AmountRule:
    """Return which of R-FI's five rules owns *xfer*'s amount.

    The TRANSFER twin of :func:`amount_rule`, and it asks the two questions in
    the same order for the same reasons: the ``amount_source_id`` COLUMN says
    whether the row owns its figure or names the relation that prices it, and
    the refinement WITHIN a declared relation is read live off the definition
    (:func:`_rule_within_transfer_definition`).

    **Three of the five rules can apply here and two cannot.**  A transfer
    takes rule 1 (its own figure), rule 3 (its definition's dated series) or
    rule 4 (a loan payment's cash, since plan step X-au-f-2 -- ruling
    **R-BAL10**).  It cannot take rule 2, which prices a paycheck and needs a
    salary profile naming a TRANSACTION template, and it cannot take rule 5,
    which is *= my parent* and a transfer has no parent transfer.  The other
    table's classifier is the mirror image: everything but rule 4.

    Args:
        xfer: The :class:`~app.models.transfer.Transfer` to classify.  Its
            ``template`` relationship is read only when it DECLARES one, so an
            undeclared transfer costs no lazy load at all.

    Returns:
        The :class:`AmountRule` that prices this transfer.

    Raises:
        AmountUnresolvable: When the transfer declares PARENT_TRANSFER -- a
            relation no transfer can name.  See
            :func:`resolve_transfer_amount`, whose refusal message states what
            wrote it.
        KeyError: When the row names a relation this dispatch has no rule for,
            exactly as :func:`amount_rule` does and for the same reason.
    """
    if xfer.amount_source_id is None:
        return AmountRule.OWN
    relation = _declared_relation(xfer.amount_source_id)
    if relation is not AmountSourceEnum.TEMPLATE:
        raise AmountUnresolvable(
            f"Transfer {xfer.id} declares amount source {relation.value!r}, "
            "and a transfer has no parent transfer for one to name. Only a "
            "transfer TEMPLATE can price a transfer; the parent-transfer "
            "relation belongs to the transactions table, where a transfer's "
            "shadow rows used it, and no shadow row exists any more. This row "
            "was stamped by a writer that confused the two tables."
        )
    return _TRANSFER_RELATION_RULES[relation](xfer)


def _is_loan_payment(xfer) -> bool:
    """Return whether *xfer* is a loan payment rather than a generic transfer.

    The fact :mod:`._loan_pricing` keys its whole live-derive machinery on: a
    :class:`~app.models.loan_payment_settings.LoanPaymentSettings` row hanging
    off the transfer's template (decision B).  A transfer with no template, or a
    template with no settings row, is an ordinary transfer -- an investment
    contribution, a savings sweep -- and rule 3 prices it from its series.

    Read live off the relationship rather than remembered, so a template
    switched between modes changes rule at that moment.

    **Its caller is :func:`_rule_within_transfer_definition` since plan step
    X-au-f-2, and it was ``_rule_within_parent_transfer`` before that.**  The
    question is unchanged -- is this transfer a loan payment? -- and what moved
    is which table's row is being classified when it is asked: the parent's,
    rather than a shadow's, per ruling **R-BAL10**.  It is therefore handed a
    transfer directly now instead of ``txn.transfer``, and the ``None`` arm
    that stood for *the shadow's parent is gone* is kept for the arm beneath
    it: a transfer with no TEMPLATE is not a loan payment either.

    Args:
        xfer: The :class:`~app.models.transfer.Transfer` being classified.

    Returns:
        ``True`` when a loan payment's settings drive this transfer's cash.
    """
    if xfer is None:
        return False
    return is_loan_payment_definition(xfer.template)


def is_loan_payment_definition(template) -> bool:
    """Return whether *template* is a loan-payment definition.

    The template half of :func:`_is_loan_payment`, and the ONE reading of the
    settings-row test (plan step **R16-b-2**, ruling **R-R67**): a
    :class:`~app.models.loan_payment_settings.LoanPaymentSettings` row hangs
    off the definition.  The row classifier asks it of its template, and the
    balance seam's estimate asks it of a definition whose row does not exist
    yet (:func:`._amount_source.definition_cash`), so the two decide rule 4
    the same way by construction.

    ``getattr`` on the relationship rather than attribute access, for the
    reason :func:`app.services.recurring_transfer_query.destination_account`
    gives for the FK column: a recurring definition is a ``TransferTemplate``
    or a ``TransactionTemplate``, the second carries no settings row at all,
    and a kind-agnostic reader answers ``False`` for it rather than raising.

    Args:
        template: A transfer template, a transaction template, or ``None``
            for a transfer with none.

    Returns:
        ``True`` when a loan payment's settings drive the definition's cash.
    """
    if template is None:
        return False
    return getattr(template, "settings", None) is not None


# WHICH RULE a declared relation refines into, keyed by the relation itself.  The
# same shape as ``_RULE_ANSWERS`` above and for the same reason: a member added
# to :class:`~app.enums.AmountSourceEnum` -- ``credit_card:CC4c``'s finance
# charge is the one already known to need one (finding **N-264**) -- raises at
# this lookup instead of silently taking whichever branch an ``if`` chain happened
# to end on.  ``tests/test_services/test_amount_source.py`` grades the table
# against the enum, so the completeness is a predicate rather than a comment.
_RELATION_RULES = {
    AmountSourceEnum.TEMPLATE: _rule_within_definition,
    AmountSourceEnum.PARENT_TRANSFER: _rule_is_parent_transfer,
}

# WHICH RULE a declared relation refines into for a TRANSFER.  It holds ONE
# member where the transaction table holds both, and the absence is the
# statement: a transfer has no parent transfer, so PARENT_TRANSFER is refused
# by :func:`transfer_amount_rule` above with a message naming the writer that
# confused the two tables, rather than reaching a lookup that would raise a
# bare ``KeyError``.  The suite grades this table against the relations a
# transfer may actually declare, so a new member arrives with a rule here too.
_TRANSFER_RELATION_RULES = {
    AmountSourceEnum.TEMPLATE: _rule_within_transfer_definition,
}
