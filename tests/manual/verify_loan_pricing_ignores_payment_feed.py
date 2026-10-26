"""Prove a loan's PRICE does not depend on the loan's own payment rows.

The regression harness for the balance step that deletes the pricing cycle:
``cash_ledger._resolve_loan_basis`` used to run
:func:`~app.services.loan_payment_service.load_loan_context` and read
``resolve_loan(...).monthly_payment`` back out, which put
:func:`~app.services.loan_payment_service.get_payment_history` on the pricing
path.  It reads the loan's TERMS alone now
(:func:`~app.services.loan_resolver.compute_monthly_payment_baseline`).

**What this file measures is the CLAIM, not the datum.**  Two sessions agreeing
that the Mortgage prices at ``1293.96`` is one measurement with more witnesses
(see the project's own lesson on this).  The claim is stronger and different:
the figure is INDEPENDENT of the payment feed.  So the harness does not merely
re-read the number -- it feeds the OLD producer four materially different
payment histories and asserts one answer, which is the only shape that can tell
"independent of the feed" from "happens to agree today".

The four feeds, per loan:

* **FULL** -- every payment ``load_loan_context`` returns.
* **EMPTY** -- no payments at all.
* **CONFIRMED ONLY** -- the settled subset, a strictly smaller feed that a
  coupled producer would price differently from FULL.
* **DOUBLED** -- every payment listed twice.  Planted, and it is the arm that
  matters: EMPTY and CONFIRMED-ONLY both SHRINK the feed, so a producer that
  read the feed only through a "latest payment" lookup could pass all three
  while still being coupled.  Doubling changes the feed's CONTENT without
  changing its span.

Then the NEW producer is run and must equal all four.

**What this file CANNOT tell you, stated because the first draft overclaimed it.**
It measures feed-INDEPENDENCE, not non-reading.  The producer this step deleted
would pass all five arms: ``resolve_loan(...).monthly_payment`` is structurally
``period_for_date(resolve_periods(params, rate_changes), as_of).period_pi``, so
it read the whole payment history and ignored it.  That is exactly the coupling
that shipped, and no value-based harness can see it.  What sees it is the
statement-counting arm in
``test_loan_payment_service.test_the_contractual_payment
.TestALoansPriceDoesNotReadItsOwnPayments``, which names the TABLE; the two
were mutation-tested and their fail sets are disjoint.
Read this file as the value half of that pair, never as the whole control.

Usage::

    PYTHONPATH=. DATABASE_URL=postgresql://.../<clone> \\
        python tests/manual/verify_loan_pricing_ignores_payment_feed.py

``PYTHONPATH=.`` is load-bearing: a script run by path puts its OWN directory on
``sys.path``, not the working directory.

Read-only: it opens no transaction of its own and writes nothing.
"""
from datetime import date

from app import create_app
from app.extensions import db
from app.models.loan_params import LoanParams
from app.services import loan_resolver
from app.services.loan_loaders import (
    load_loan_anchor_facts,
    load_loan_params,
    load_rate_changes,
)
from app.services.loan_ledger import LoanCalendars
from app.services.loan_payment_service import load_loan_context
from app.services.rate_period_engine import period_for_date
from app.services.balance_at import BalanceContext


def _old_answer(params, anchors, payments, rate_changes, as_of):
    """Run the producer this step deleted, against an arbitrary payment feed.

    Args:
        params: The loan's :class:`LoanParams` row.
        anchors: Its anchor facts.
        payments: The payment feed to price against -- the variable under test.
        rate_changes: The loan's rate-change feed.
        as_of: The evaluation date.

    Returns:
        The ``monthly_payment`` the old ``_resolve_loan_basis`` would have read.
    """
    return loan_resolver.resolve_loan(
        loan_resolver.LoanInputs(params, anchors, payments, rate_changes),
        as_of,
    ).monthly_payment


def main():
    """Print one line per loan per feed, and a verdict line."""
    app = create_app()
    with app.app_context():
        as_of = date.today()
        rows = db.session.query(LoanParams).order_by(LoanParams.account_id).all()
        print(f"# as_of={as_of} loans={len(rows)}")
        verdict = True
        for lp in rows:
            account_id = lp.account_id
            params = load_loan_params(account_id)
            anchors = load_loan_anchor_facts(params)
            ctx = BalanceContext.build(lp.account.user_id)
            context = load_loan_context(
                account_id, ctx.amounts(), params,
            )
            full = list(context.payments)
            confirmed = [p for p in full if p.dates.is_confirmed]
            feeds = {
                "FULL": full,
                "EMPTY": [],
                "CONFIRMED_ONLY": confirmed,
                "DOUBLED": full + full,
            }
            answers = {}
            for name, feed in feeds.items():
                answers[name] = _old_answer(
                    params, anchors, feed, context.rate_changes, as_of,
                )
                print(
                    f"  account={account_id} feed={name:<14} "
                    f"n={len(feed):>3} monthly_pi={answers[name]}"
                )
            basis = LoanCalendars().loan_calendar_of(account_id)
            # The basis holds the loan's PERIOD SET since plan step X-au-g-2b
            # (ruling R-IJ), so the figure this harness compares is the one
            # governing ``as_of`` -- the same date every other arm reads.
            # It is the loan's calendar since plan step recurrence:R25, which
            # deleted ``_resolve_loan_basis`` for the pass's LoanCalendars.
            new = None if basis is None else period_for_date(
                basis.periods, as_of,
            ).period_pi
            cheap = loan_resolver.compute_monthly_payment_baseline(
                params, load_rate_changes(account_id), as_of,
            )
            print(
                f"  account={account_id} feed={'NEW (no feed)':<14} "
                f"n={0:>3} monthly_pi={new}"
            )
            distinct = set(answers.values()) | {new, cheap}
            ok = len(distinct) == 1
            verdict = verdict and ok
            print(
                f"# account={account_id} distinct_answers={len(distinct)} "
                f"{'INDEPENDENT' if ok else 'COUPLED -- ' + str(sorted(map(str, distinct)))}"
            )
        print(f"# VERDICT: {'PASS' if verdict else 'FAIL'}")


if __name__ == "__main__":
    main()
