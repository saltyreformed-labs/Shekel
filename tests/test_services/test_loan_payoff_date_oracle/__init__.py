"""C8b oracle: the derived payoff date is a fold to zero.

Plan step C8b (``docs/audits/balance_architecture/README.md``).  The seam's
``balance_at.loan_payoff_date`` folds a loan's forward plan from its
confirmed-present seed and returns the date the balance reaches ``0.00`` -- the
DERIVED payoff the arc substitutes for the blind-schedule copies
(``LoanState.payoff_date``, ``RecurrenceRule.end_date``).  This proves it two
ways:

* :class:`TestPlanPayoffDate` -- the pure fold-to-zero on hand-built plans
  (``plan_payoff_date`` from ``tests/oracles/loan_forward_fold``: the retired
  ``(seed, plan)`` contract over the ONE timeline since plan step
  recurrence:R16-c-1, running the production replay), so the reaches-zero /
  retired-seed / negative-amortization / due-order rules are pinned to
  arithmetic anyone can check.
* :class:`TestLoanPayoffDateSeam` -- the seam entry against the engine's OWN
  contractual projection (``compute_payoff_scenarios``' contractual slice and
  ``project_forward`` with the extra, an independent producer) on real loans,
  so a HEALTHY or OVERPAYING loan's derived payoff EQUALS the contract's (those
  cutovers at C8c move no baseline) and a standing extra beats the contractual
  date (the fold-to-zero, not ``plan[-1].date``).

**Baseline parity is a healthy/overpaying claim, NOT universal.** An UNDERPAYING
loan (a balance behind the contractual schedule) its recurring payment pays clears
a few months PAST the contractual date -- the payment's occurrences past the
contract (C8c, :class:`TestPayoffTailExtension`) -- so it derives a LATER date than
the resolver's ``is_last_month``-forced contractual one (the deliberate move C8c
makes).  Only a drift too severe to clear within that window folds to ``None``.  A
loan with NO recurring payment is planned as its contract line and closes on the
contractual date with the balloon (ruling plan#3982).
The PURE fold-to-zero on a truncated plan (``plan_payoff_date`` handed a plan with
no further installments) still returns ``None``, pinned below; the drift that
produces an underpayment is what C7's payment-drift warning surfaces.

The producer is ADDITIVE and UNWIRED at C8b -- only this oracle reads it.
"""
