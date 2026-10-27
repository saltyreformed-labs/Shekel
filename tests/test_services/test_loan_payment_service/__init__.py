"""
Tests for the loan payment service.

Verifies that get_payment_history() correctly reads the payments into a
loan -- the to-side legs of transfers into it -- applies the right filters,
prices each one (a settled leg at the figure its transfer stores, a projected
one at what its transfer resolves to), and carries each payment's three
dates: the pay period funding it, the installment it satisfies, and -- since
plan step X-an -- the stored day its cash moved, which is what
``is_confirmed`` is derived from.

Also tests the payment preparation utilities (compute_contractual_pi
and prepare_payments_for_engine) that correct escrow inflation and
biweekly month overlaps before passing payments to the amortization
engine.
"""
