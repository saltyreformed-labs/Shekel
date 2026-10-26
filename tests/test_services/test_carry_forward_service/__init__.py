"""
Dedicated unit tests for app.services.carry_forward_service.

Fills coverage gaps not addressed by existing carry-forward tests in
test_credit_workflow.py, test_workflows/, test_hostile_qa.py, and
test_idempotency.py.  One part per subject: what carry_forward_unpaid
moves and what it leaves (test_what_carries), a transfer
(test_a_transfer_carries), the override sibling
(test_the_override_sibling), an envelope's leftover and where it lands
(test_the_envelope_leftover, test_the_envelope_target), a batch across
kinds (test_the_batch_and_its_invariants), the preview (test_the_preview,
test_the_preview_agrees), and the date a leftover row takes
(test_a_leftover_row_is_dated).
"""
