"""
Tests for the loan payment service.

Verifies that get_payment_history() correctly queries shadow income
transactions, applies the right filters, uses effective_amount for
the payment amount, and carries each shadow's three dates: the pay period
funding it, the installment it satisfies, and -- since plan step X-an -- the
stored day its cash moved, which is what ``is_confirmed`` is derived from.

Also tests the payment preparation utilities (compute_contractual_pi
and prepare_payments_for_engine) that correct escrow inflation and
biweekly month overlaps before passing payments to the amortization
engine.
"""
