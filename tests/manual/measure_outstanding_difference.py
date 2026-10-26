"""Re-derive every account's OUTSTANDING DIFFERENCE, and corroborate it.

The measurement plan step **balance:X-f3c-4** must take before it books
anything, and the reason it is a script rather than a number written down:
**the figure is the NET of every assertion's correction, so it moves whenever
the owner declares a balance.**  Four undated values for that one quantity were
once spread across the corpus, none contradicting the others -- they were one
number measured on four dates.  Finding N-171 still carries
two of them, undated: striking them, because a figure in an INDEX cannot be made
safe and can only be taken out, was done only on the unmerged branch
``feat/x-f3c-4``.

**Run this against a restore, never against a live database**, and quote its
output with the database and the date beside it.

It prints TWO independently-derived answers per account and they must agree to
the cent:

* ``balance_at.cash_outstanding_difference`` -- the balance seam's own fold,
  the owner's latest declared balance less its stored opening equity plus every
  posting through that day;
* the persisted double-entry ledger's ``account_trueup`` net on that account's
  linked ledger account -- written by ``account_posting_service`` and read here
  by SQL that touches ``balance_at`` at no point.

**Two producers sharing no code is the whole value of this file.**  A single
producer re-read twice is one measurement with more witnesses, which is the
shape ``lessons.md`` names; the ledger leg is a genuinely separate derivation of
the same quantity, and its counter leg on ``<account> -- Opening``
(``anchor_equity``) IS finding **N-171** -- the real economic activity the
cutover moves onto the income statement.

**It also prints the OFFER GATE's inputs**, because the gate is what decides
whether the figure may be acted on at all: whether an import reconciles the
span, and whether the bank's own record corroborates the opening.  As of
2026-09-01 production holds 0 statement imports, 0 bank lines and 0 matches, so
the gate refuses on every account -- see
``docs/design/cash_difference_acceptance_audit.md`` for why the gate as
specified is refuted, and what that means for what this figure is FOR.

Usage::

    DATABASE_URL=postgresql://.../<a restore> \\
        PYTHONPATH=. python tests/manual/measure_outstanding_difference.py

Reads only.  No writes, no commit, nothing staged.
"""

from decimal import Decimal

from app import create_app, ref_cache
from app.enums import LedgerAccountKindEnum
from app.extensions import db
from app.models.account import Account
from app.models.user import User
from app.services import balance_at, outstanding_difference

#: The posted ledger's own answer for one account, by SQL that reaches the
#: balance seam nowhere.
#:
#: **It narrows to the LINKED ledger row by KIND, and that narrowing is the
#: whole correctness of this file.**  TWO ledger accounts carry the same
#: ``account_id`` -- the linked one and the ``<account> -- Opening``
#: ``anchor_equity`` one -- and a correction is a BALANCED entry across both.
#: A query keyed on ``account_id`` alone therefore sums both legs and prints
#: ``0.00`` for every account, which READS AS AGREEMENT and is the report
#: manufacturing its own confirmation.  Plan step X-f3c-3's adversarial review
#: caught exactly that query; this file reproduced it, and the first run refused
#: to agree with the seam, which is what a corroboration is for.
#:
#: The kind arrives as an INTEGER id through ``ref_cache``, never as the
#: ``name`` string (``CLAUDE.md``: ref tables are IDs for logic, strings for
#: display).
_LEDGER_SQL = """
SELECT ps.name AS source, sum(p.amount) AS net
FROM budget.account_postings p
JOIN budget.journal_entries je ON je.id = p.journal_entry_id
JOIN budget.ledger_accounts la ON la.id = p.ledger_account_id
JOIN ref.posting_sources ps ON ps.id = je.source_kind_id
WHERE la.account_id = :account_id
  AND la.kind_id = :linked_kind_id
  AND ps.name IN ('account_opening', 'account_trueup')
GROUP BY ps.name
"""


def _ledger_trueup_net(account_id: int) -> Decimal:
    """Return the ``account_trueup`` net on an account's LINKED ledger.

    Args:
        account_id: The real account whose linked ledger to sum.

    Returns:
        The signed net, ``Decimal("0.00")`` when the account has no correction.
    """
    rows = db.session.execute(
        db.text(_LEDGER_SQL),
        {
            "account_id": account_id,
            "linked_kind_id": ref_cache.ledger_account_kind_id(
                LedgerAccountKindEnum.LINKED,
            ),
        },
    ).all()
    return next(
        (row.net for row in rows if row.source == "account_trueup"),
        Decimal("0.00"),
    )


def _report_account(account: Account, ctx) -> None:
    """Print one account's difference, its corroboration and its gate inputs.

    Args:
        account: The account to measure.
        ctx: The read pass's ``BalanceContext``.
    """
    resolved = outstanding_difference.outstanding_difference(account, ctx)
    if resolved is None:
        print(f"  {account.id:>3} {account.name[:30]:<30} -- question does not "
              f"apply (a MODELLED kind or a loan; ruling R-FO)")
        return
    diff, span = resolved.difference, resolved.difference.span
    ledger = _ledger_trueup_net(account.id)
    agree = "AGREE" if ledger == diff.amount else "*** DISAGREE ***"
    print(f"  {account.id:>3} {account.name[:30]:<30}")
    print(f"      books open {diff.opened_on} at {diff.opening_equity:>12}")
    print(f"      asserted   {diff.asserted_on} at {diff.asserted:>12}")
    print(f"      books produce            {diff.books:>12}")
    print(f"      OUTSTANDING DIFFERENCE   {diff.amount:>12}")
    print(f"      ledger account_trueup    {ledger:>12}   {agree}")
    print(f"      span {span.first_day}..{span.last_day} "
          f"empty={span.is_empty}")
    if resolved.reconciliation is None:
        print("      reconciliation: NO import recorded for this account")
    else:
        rec = resolved.reconciliation
        print(f"      days={rec.day_count} compared={rec.compared} "
              f"unchecked={rec.unchecked} unimported={rec.unimported} "
              f"disagreeing={rec.disagreeing} reconciles={rec.reconciles}")


def main() -> None:
    """Measure every account of every owner and print the report."""
    app = create_app()
    with app.app_context():
        for user in db.session.query(User).order_by(User.id).all():
            accounts = (
                db.session.query(Account)
                .filter(Account.user_id == user.id)
                .order_by(Account.id).all()
            )
            print(f"=== user {user.id} -- {len(accounts)} account(s) ===")
            if not accounts:
                continue
            ctx = balance_at.BalanceContext.build(user.id)
            for account in accounts:
                _report_account(account, ctx)


if __name__ == "__main__":
    main()
