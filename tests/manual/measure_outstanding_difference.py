"""Re-derive every account's OUTSTANDING DIFFERENCE, and its offer gate's inputs.

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

It prints the balance seam's own fold per account,
``balance_at.cash_outstanding_difference`` (through
``outstanding_difference.outstanding_difference``) -- the owner's latest
declared balance less its stored opening equity plus every posting through that
day.

**It no longer corroborates that answer.**  Until plan task plan#3080 it printed
a second, independently-derived one beside it, the persisted double-entry
ledger's ``account_trueup`` net on the account's linked ledger account, written
by ``account_posting_service`` and read by SQL that touched ``balance_at`` at no
point, and the two had to agree to the cent; that ledger leg's counter leg on
``<account> -- Opening`` (``anchor_equity``) IS finding **N-171**.  The stored
ledger's writers went at plan task plan#3133, and the worked-out ledger that
replaced it books each account's corrections off the seam's own replay
(``ledger_lines._account_walk``, plan task plan#2525, rule 14), so its
``account_trueup`` net is the seam's answer read a second time: one measurement
with more witnesses, the shape ``lessons.md`` names, never a corroboration.  No
second producer of this figure remains.

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

from app import create_app
from app.extensions import db
from app.models.account import Account
from app.models.user import User
from app.services import balance_at, outstanding_difference


def _report_account(account: Account, ctx) -> None:
    """Print one account's difference and its gate inputs.

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
    print(f"  {account.id:>3} {account.name[:30]:<30}")
    print(f"      books open {diff.opened_on} at {diff.opening_equity:>12}")
    print(f"      asserted   {diff.asserted_on} at {diff.asserted:>12}")
    print(f"      books produce            {diff.books:>12}")
    print(f"      OUTSTANDING DIFFERENCE   {diff.amount:>12}")
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
