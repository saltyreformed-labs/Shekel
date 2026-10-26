"""
Shekel Budget App -- A hidden row: what a refusal may say about a row no screen shows.

Plan step ``credit_card:CC-5-4a-4``.  A deleted row takes no money (ruling
**R-CC89**), and a stale page's Mark Paid, Save or add purchase on one is told
so by name (rulings **R-CC101**, **R-CC104**; never by id, **R-CC98**).  Two
acts hide a row -- the row's own delete, and its recurring item's archive,
which hides the item's empty Projected rows
(``routes/templates/crud._soft_delete_projected_rows``) and whose un-archive
brings them back unless the books have moved over them (rulings **R-CC86**,
**R-PC95**, **R-PC99**; the delete dialog says so, **R-CC112**) -- and the
sentence says whether the
row's recurring item is archived, whichever act hid it (ruling **R-CC107**,
developer 2026-09-24, "Say archived": *"When the row's recurring item is
archived, the sentence says 'Gym was archived: a payment cannot be recorded
under it.  Reload the page.' (and the same for a Save or a purchase). A row you
deleted still says 'was deleted'. If you deleted one row and later archived the
whole item, it says 'archived', which is also true."*).

It lives in ``app/utils`` rather than beside the ownership door that first
answered with a name (``auth_helpers``, which imports Flask) because the
services that refuse the same rows -- the status seam, the settle verbs, the
purchase door -- say the same sentence, and a service imports no Flask
(``CLAUDE.md`` Architecture).
"""

from dataclasses import dataclass

from app.extensions import db
from app.models.transaction import Transaction
from app.models.transfer import Transfer
from app.services.transfer_legs import leg_label


@dataclass(frozen=True)
class HiddenRow:
    """A hidden row: its name, and whether its recurring item was archived.

    What a door holds for a row it refuses because it is hidden, and all it
    holds: the NAME, so a door holding one has no row to write money under --
    the refusal ruling **R-CC89** exists for stays structural rather than a
    check each door must remember -- and whether its recurring item is
    archived, so the sentence can say so (ruling **R-CC107**).  Every sentence
    the transaction doors, the status seam and the settle verbs say about a
    hidden transaction row takes one: the seam's
    ``deleted_row_payment_refusal``,
    ``entry_service.deleted_row_purchase_refusal`` and the Save door's
    ``routes/transactions/_helpers._deleted_row_change_refusal``.  Other
    sentences about a hidden row take none, among them: the database's own
    refusal (``app/deleted_row_infrastructure``, which only a writer that
    skips every door meets), which says "was deleted" whether or not the
    item is archived (finding **CC-377**); the transfer service's settle
    refusal of a deleted transfer (``transfer_service._settle``), which no
    screen reaches because the transfer's door answers "not found" first;
    and Mark
    Credit's "not found" for a deleted row
    (``credit_workflow.mark_as_credit``), which never reaches a screen --
    the route answers ruling **R-CC89**'s bare "not found" (**R-CC99** (b)).

    Attributes:
        name: The row's name (ruling **R-CC98**: never its id).
        archived: Whether the row's recurring item is archived.  ``False``
            for a row gone from the table: a one-off's delete removes its row,
            and nothing is archived by removing a row.
    """

    name: str
    archived: bool = False

    @property
    def went(self) -> str:
        """The sentence's verb: ``"was archived"`` or ``"was deleted"``.

        ``"was archived"`` where the row's recurring item is archived, however
        the row was hidden; ``"was deleted"`` otherwise.
        """
        return "was archived" if self.archived else "was deleted"

    @classmethod
    def of(cls, row: Transaction | Transfer) -> "HiddenRow":
        """Return what a refusal may say about *row*, a hidden row still in the table.

        **The ONE producer of** :attr:`archived`: the row's recurring item is
        inactive.  A row whose item is archived says so, however it was hidden
        (ruling **R-CC107**'s last sentence).  A row with no item -- a CC
        payback, a transfer shadow -- is never archived.

        **Read off the row's own definition** (``row.template``), as the
        request's owner lock left it.  Until plan step ``balance:X-bn`` it was
        read FRESH, by a statement of its own, because a row that loaded its
        definition before a door's row lock kept it, and an archive that
        committed while the door waited for that lock (the race ruling
        **R-CC96** let a door see) would still read active -- measured by
        Mark Paid racing the archive.  The owner's write lock (ruling
        **R-CC106**) now precedes every read a writing transaction makes of
        the owner's data, so an archive from another tab either committed
        before the row and its definition were loaded in the transaction that
        reads them, or waits for THAT TRANSACTION to end -- not the request's:
        a commit or rollback expires both, and the next transaction re-reads
        them under its own lock (finding BAL-565 is what a request does
        between two of them).

        **Nothing it reads flushes**: the whole body is under
        ``no_autoflush``, the row's own columns included, so an EXPIRED row
        refreshes without writing and asking writes none of the caller's
        staged state.  The three settle verbs ask it at their first check,
        through ``reject_unsettleable``, before any read that would flush, so
        a call refused at that check leaves a caller's staged state
        unwritten.  An autoflushing read here
        broke the first check for a row loaded deleted beside a staged change,
        and all three verbs flushed it before raising (review 7 of this step,
        measured 2026-09-24), and a read of an expired row's columns outside
        the guard did the same (review 8); both are graded by
        ``test_cc5_4a4_hidden_row_doors.TestTheHiddenRowsWordsFlushNothing``.
        What it reads is the loaded definition's ``is_active``, so a caller
        that staged an archive would read the item archived.  None does: the
        two route modules that write ``TransactionTemplate.is_active``
        (``routes/templates/crud``, ``routes/salary/profiles``) reach no
        caller of this, directly or through the services they call, before
        their commit (a caller census, 2026-09-24).

        **A transfer is told the same way** (plan step
        ``balance:X-bi-6-4d-2``, finding **BAL-547**): "was archived" when
        its recurring transfer is archived -- the archive hides its empty
        Projected transfers (``routes/transfers/lifecycle``) -- and by its
        from-side's LEG LABEL ("Transfer to Savings",
        ``transfer_legs.leg_label`` over its endpoints' current names), the
        plan of record's wording, never the nullable ``Transfer.name``,
        which would print "None was deleted".  The from-side's and not the
        to-side's because the arm refuses the transfer once, before either
        side is written, and the from-side comes first where
        ``transfer_service._side_days.repair_fallback`` orders the two
        sides for one answer.  The
        archive writes ``TransferTemplate.is_active`` and soft-deletes
        through ``transfer_service.delete_transfer``, which hands the status
        seam no record, so no caller of this sees an archive it staged
        itself.

        Args:
            row: A session-attached row the caller has found hidden: a
                ``Transaction``, or a ``Transfer`` (the status seam's
                Transfer arm, ``reject_settlement_on_a_deleted_row``).

        Returns:
            The row's name (a transfer's from-side leg label), and whether
            its item is archived.
        """
        with db.session.no_autoflush:
            template = row.template
            name = (
                leg_label(row.from_account, row.to_account)[0]
                if isinstance(row, Transfer) else row.name
            )
            return cls(
                name, archived=template is not None and not template.is_active,
            )
