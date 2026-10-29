"""
Shekel Budget App -- Transfer Service: WHO OWNS a transfer's stated figure.

One act: a caller states a transfer's amount OWNERSHIP, and this applies it to
the transfer.  Ruling **R-FI**'s two states.  It applied the same answer to
each of the transfer's two twin rows as well until plan step
``balance:X-bi-6-4d-2`` deleted them; that step's leaf 3b deleted the loop.

**The caller states ONE value since plan step X-au-f, and ruling R-BAL11 is
why.**  It took three parameters -- a figure, whether a human authored it, and
the conflict resolver's explicit hand-back -- which are three spellings of one
question, *what prices this row*.  A trace measured X-au-f's own claim that the
third dissolves when ``budget.transfers.amount`` empties as FALSE: on its own
that parameter becomes the ONLY spelling of the hand-back.  What dissolves all
three is the caller stating an
:class:`~app.models.amount_ownership.AmountOwnership`, where *the owner's, and
here is the figure* and *its definition's* are one value and ABSENT means this
save says nothing about the amount.

**It is a module of its own for the reason ``._endpoints`` and ``._status``
are** (plan step X-au-g-2c-2): ``._update`` ORCHESTRATES the doors, and every
concern that carries a rule of its own lives beside it rather than inside it.
The amount arm was four lines in that orchestration while a shadow held a COPY
of its parent's figure; it carried a decision from that step (which answer
each twin took) and it still carries a refusal (:func:`grade_amount_ownership`),
and a rule with an argument behind it belongs where the argument can be read.

Flask-isolated like the rest of the package: ORM rows in, nothing out.  Mutates
in place; does not flush and does not commit.
"""

from app.models.amount_ownership import AmountOwnership
from app.models.transfer import Transfer
from app.services.transfer_service._validation import _validate_positive_amount


def apply_amount_ownership(transfer: Transfer, ownership) -> None:
    """Apply one stated AMOUNT OWNERSHIP to the transfer.

    Called for every update that states one.  Ruling **R-FI**'s two states and
    ruling **R-BAL11**'s one parameter.

    **The transfer takes the stated ownership verbatim.**  It is the row the
    caller is talking about, and since plan step X-au-f it is also the row that
    decides owner-or-contract for the whole pair (ruling **R-IO**): a figure
    here is the owner's, an absent one hands the pair to the definition that
    generated it.  Each side's leg is priced off the transfer while it is
    planned (:class:`~app.services.transfer_legs.TransferLeg` stores no
    figure), and once settled off the transfer's one settled figure (a
    side's record holds none).

    **A twin row's ownership FOLLOWED the parent's** until plan step
    ``balance:X-bi-6-4d-2`` deleted the twins (its leaf 3b deleted the loop
    that wrote it): a twin COPIED an owned figure, and declared
    ``PARENT_TRANSFER`` under a derived one (a member plan step
    ``balance:X-bi-6-5`` deleted).

    **A THIRD answer the twins once had is gone because it has no caller left
    to express it.**  It was *say nothing and leave each leg as it stands*, and
    it existed because
    a bare ``is_override`` flag could reach this function carrying no figure at
    all.  A caller states an ownership or it does not; ABSENCE is now expressed
    by not calling this at all (``._update`` asks whether the key is present),
    so there is no arm here that can revoke an ownership it was not asked to
    judge.  **That arm's absence is what cost `$174.10` twice** -- on a
    DERIVE-mode loan payment, where handing a leg back reverted the owner's
    ``$1,325.00`` to the contract's ``$1,499.10`` -- once through the FORM's
    period move and once through an UNRELATED-FIELD save, and both are graded by
    ``test_the_FORMs_period_move_does_not_HAND_BACK_a_taken_leg`` and
    ``test_an_UNRELATED_field_save_does_not_HAND_BACK_a_taken_leg``.  Those
    tests now describe a door that cannot reach this function without meaning
    to.

    **Reading ``is_override`` here is gone with the parameter that carried it.**
    The flag means exactly one thing since plan step X-au-h -- *this row is the
    OWNER's, not the rule's* -- and what this function wanted was never the flag
    but *what prices this row*, which the caller now says outright.  The
    conflict resolver's explicit hand-back is spelled as
    ``derived_ownership(TEMPLATE)`` like every other statement that the
    definition prices the pair.

    Args:
        transfer: The transfer.
        ownership: The :class:`~app.models.amount_ownership.AmountOwnership`
            the caller states for the PARENT -- ``own`` over a figure a human
            authored, or ``derived`` naming
            :attr:`~app.enums.AmountSourceEnum.TEMPLATE` when its definition
            prices it.  Never ``None``: a caller with nothing to say about the
            amount does not call this.
    """
    transfer.amount_ownership = ownership


def grade_amount_ownership(updates: "dict[str, object]") -> None:
    """Refuse a malformed ``amount_ownership`` and write its figure coerced.

    The GRADE before :func:`apply_amount_ownership`'s apply, which
    ``._update`` asks ahead of every write: hoisted there by plan step
    R10-b's adversarial review:
    it ran at the arm that assigns it, two writes later, so an update that
    moved the pair between accounts and stated a negative figure reversed a
    loan payment's split BEFORE deciding the amount was illegal.

    Why the caller states ONE value (ruling **R-BAL11**) is this module's
    docstring.

    **READ rather than POPPED, and a first revision of this step popped it.**
    TWO things downstream read ``updates.keys()``: the posting reconcile's
    ``_POSTING_RELEVANT_FIELDS`` test and the audit's ``fields_changed``.
    Popping took the amount out of both, so a settled transfer's re-price
    stopped reconciling its ledger and vanished from the audit trail. Nothing
    here applies fields by name, so the key rides harmlessly.

    **Presence is the question, not a sentinel value.** A first revision used
    one, reasoning from ``AmountOwnership``'s own rule that ``None`` means
    "stated nothing" on the ATTRIBUTE -- but that ambiguity is the mapped
    column pair's, and a kwargs dict answers "was this stated" by key. The
    sentinel also turned an explicit ``amount_ownership=None`` into an
    ``AttributeError``, where the parameter it replaced deliberately REFUSED
    the analogous absence with a message.

    **The COERCED value is what gets written.** ``_validate_positive_amount``
    returns ``Decimal(str(amount))``, and a first revision validated the
    figure and then applied the caller's raw ownership -- so one input
    produced two values and a ``float`` handed in here would have reached a
    ``Numeric(12,2)`` column. Unreachable from the routes, which load
    Marshmallow ``Decimal``s, and a money-type guard the code deliberately had.

    Args:
        updates: The update kwargs as submitted; an owned figure is replaced
            in place by its coerced value.

    Raises:
        ValueError: On an explicit ``amount_ownership=None``.
        ValidationError: On a non-positive owned figure.
    """
    if "amount_ownership" not in updates:
        return
    ownership = updates["amount_ownership"]
    if ownership is None:
        raise ValueError(
            "update_transfer was given amount_ownership=None. A save that "
            "says nothing about the amount OMITS the key (ruling R-BAL11); "
            "an explicit None is a half-written statement, and applying it "
            "would leave the transfer owning neither a figure nor a "
            "relation -- the state ck_transfers_amount_ownership refuses."
        )
    if ownership.figure is not None:
        updates["amount_ownership"] = AmountOwnership.own(
            _validate_positive_amount(ownership.figure),
        )
