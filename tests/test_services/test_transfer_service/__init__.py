"""
Shekel Budget App -- Transfer Service Tests

Comprehensive tests for create_transfer, update_transfer, and
delete_transfer: what each door writes on the transfer's row, validation
rules, edge cases, and cross-user isolation.

A transfer is one stored row since plan step ``balance:X-bi-6-4d-2``, and its
two sides are read off it every time, so a door's test checks what the door
WROTE, re-read from the database, and the side's own payment record, the one
thing still stored per side (ruling R-BAL290 part 1, the developer,
2026-10-14).  The edit group, :class:`TestUpdateTransfer`, reads so since
that step's checkpoint 2e-2b-1b-1, and the create and figure groups,
:class:`TestCreateTransfer` and :class:`TestTheFigureCorrectionDoorOnAPair`,
since its checkpoint 2e-2b-1b-2, and the move group,
:class:`TestMovingATransferBetweenAccounts`, since its checkpoint 2e-2b-1b-3,
and the restore and due-date groups, :class:`TestRestoreTransfer` and
:class:`TestTheDueDateAndEachSidesSettleDay`, since its checkpoint 2e-2b-2b.  The
delete group, :class:`TestDeleteTransfer`, holds one case per state of the
transfer and kind of delete since that step's checkpoint 2e-2b-2a (ruling
R-BAL290 part 2), where it took in the already-hidden and paid groups.  What
each side shows of a Projected or
Cancelled row is the ``test_transfer_legs`` package's
``TestBothSidesShowTheTransfer``, and a settled row's price that package's
``TestTheLegMapProducers``'
``test_both_legs_of_a_settled_transfer_record_its_figure_beside_the_plan_and_retain_nothing``
and ``TestALegIsValuedAsItsParent``'
``test_a_settled_from_side_leg_is_worth_the_transfers_figure_and_not_its_plan``.
One leg per side is that package's
``TestTransferLegs::test_each_account_gets_its_own_side``, and which endpoint
each side is on its ``TestLegOf`` and the projection table.  The database
refuses a second side row: the expense side's in
``test_a_transfer_is_one_row/test_the_upgrade.py``'s ``test_the_database_refuses_a_twin``,
both sides' in ``test_a_transfers_amount_is_declared/test_the_declaration.py``'s
``test_the_transfer_owns_its_figure_and_neither_twin_can_be_stored``.  The
``TestInvariants`` class that checked them here was dissolved into those
tests and this file's edit group at checkpoint 2e-2b-1b-1.
"""
