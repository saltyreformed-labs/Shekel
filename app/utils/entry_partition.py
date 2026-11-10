"""
Shekel Budget App -- Transaction-entry partition helper

Single source of the "which purchases does the CC Payback repay" rule.  A
transaction's individual purchase entries split into two groups by the
``is_credit`` flag: flagged entries flow through a separate CC Payback
transaction, while the rest do not.

**It is the PAYBACK's partition, and no longer the screen's** (plan task
plan#3143).  The flag was also what the grid cell's "includes $X on CC"
split on, through ``entry_service.compute_entry_sums``, until the CC tick
began filing a purchase ON the card with no flag and no payback (plan task
plan#2930): that sum now reads
:func:`app.services.movement_account.on_a_card`, the flag or a card other
than the row's account, while the payback must still repay the flagged
lines alone, or it would pay the card's purchases a second time beside the
card's own payment.

The one reader is ``entry_credit_workflow`` (sizing and linking the
payback), which cannot import from ``entry_service`` because of an import
cycle: ``entry_service`` imports ``entry_credit_workflow.sync_entry_payback``.
This leaf util -- importing only the model -- sits below both (DH-#75).
"""

from app.models.transaction_entry import TransactionEntry


def partition_entries(
    entries: list[TransactionEntry],
) -> tuple[list[TransactionEntry], list[TransactionEntry]]:
    """Split entries into ``(debit_entries, credit_entries)`` by ``is_credit``.

    Pure function -- no database access.  The single definition of the
    flagged-vs-unflagged partition the CC Payback is sized and linked from
    (:mod:`app.services.entry_credit_workflow`, which needs the flagged list
    itself).  The return order mirrors
    :func:`app.services.entry_service.compute_entry_sums`'s
    ``(sum_debit, sum_credit)`` convention, though that sum splits on a card
    rather than on the flag since plan task plan#3143 (module docstring).

    Args:
        entries: An iterable of :class:`TransactionEntry` rows, each
            exposing ``is_credit`` (bool).

    Returns:
        A ``(debit_entries, credit_entries)`` tuple of lists; an entry is
        a credit iff ``entry.is_credit`` is true.
    """
    debit_entries: list[TransactionEntry] = []
    credit_entries: list[TransactionEntry] = []
    for entry in entries:
        if entry.is_credit:
            credit_entries.append(entry)
        else:
            debit_entries.append(entry)
    return debit_entries, credit_entries
