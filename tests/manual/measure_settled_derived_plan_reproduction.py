"""Does the live derivation REPRODUCE the plan the amount-source cutovers deleted?

The evidence behind the ``/analytics/spending`` fix of 2026-09-05.  The cutovers
(``d7b2e6c1a483`` salary, ``c8f3a5d2e714`` template, ``c9a4e7b21d58`` transfer
shadow) declare a row's plan DERIVED and empty ``estimated_amount``.  The
spending report's surprises list wants a SETTLED row's plan, so the question
that decides whether routing it to ``resolve_transaction_amount`` is faithful or
merely quiet is: for the settled rows those cutovers emptied, does the resolver
answer what the column used to hold?

**The pre-cutover figure comes from ``system.audit_log``, and that is exact
rather than reconstructed.**  ``budget.transactions`` is an audited table and the
trigger is per-ROW on UPDATE, so each cutover's raw ``op.execute`` wrote an audit
row whose ``old_data->>'estimated_amount'`` IS the figure it deleted.  The
declared set is identified by ``changed_fields`` being exactly
``{amount_source_id,estimated_amount}`` -- the pair only a cutover writes.

**IT PARTITIONS BY RULE AND BY TYPE, AND THE PARTITION IS THE POINT.**  A first
pass of this measurement reported "109 of 109 settled EXPENSE rows reproduce"
and that headline was WRONG twice over: 109 was the count over EVERY settled
declared row, income included, and stating it as an expense denominator inflated
the population the spending report actually reads by every income row that
happened to agree.  An adversarial review caught it by reconciling against the
three cutovers' own censuses (350 + 59 + 525 = 934 declared) and showing the
expense subset could not exceed 97.  A denominator that mixes populations reads
as MORE thorough, which is why the per-rule and per-type counts are printed
rather than summarised -- the sibling ``verify_amount_resolver.py`` records the
same trap in its stronger form, where an agreement oracle over rows whose answer
IS the stored column cannot fail at all.

Usage::

    DATABASE_URL=postgresql://... VALKEY_PASSWORD=unused \\
        PASSKEY_ORIGIN=https://shekel.test PYTHONPATH=. python \\
        tests/manual/measure_settled_derived_plan_reproduction.py

``VALKEY_PASSWORD``: ``create_app("production")`` refuses to build without the
rate-limit store's password from plan#2634 on, and a tree before it reads none; this
harness reaches no store, so any value serves both sides.  ``PASSKEY_ORIGIN`` likewise
from plan#3488 on, which must be an origin as a browser writes one; this harness runs no
passkey ceremony, so any such origin serves both sides.

Prints one line per (rule, type) cell plus every row that does not reproduce.
"""

import json
import os
import sys
from collections import defaultdict
from datetime import date
from decimal import Decimal

sys.path.insert(0, os.getcwd())

from app import create_app, db  # noqa: E402  pylint: disable=wrong-import-position
from app.enums import TxnTypeEnum  # noqa: E402  pylint: disable=wrong-import-position
from app import ref_cache  # noqa: E402  pylint: disable=wrong-import-position
from app.models import Transaction  # noqa: E402  pylint: disable=wrong-import-position
from app.services.balance_at import BalanceContext  # noqa: E402  pylint: disable=wrong-import-position
from app.services.cash_ledger import (  # noqa: E402  pylint: disable=wrong-import-position
    derived_amount_basis,
    amount_rule,
    resolve_transaction_amount,
)

#: The pair of columns only an amount-source cutover writes together.
_DECLARED = "{amount_source_id,estimated_amount}"


def main() -> int:
    """Measure and print the partition.  Returns a process exit code."""
    app = create_app("production")
    with app.app_context():
        rows = db.session.execute(db.text(
            "SELECT a.row_id, a.old_data->>'estimated_amount' AS old_plan, "
            "       t.transaction_type_id, t.status_id, t.name "
            "  FROM system.audit_log a "
            "  JOIN budget.transactions t ON t.id = a.row_id "
            " WHERE a.table_name = 'transactions' "
            "   AND a.changed_fields::text = :declared "
            "   AND EXISTS (SELECT 1 FROM budget.transaction_entries e "
            "               WHERE e.transaction_id = t.id AND e.covers_settlement) "
            " ORDER BY a.row_id"
        ), {"declared": _DECLARED}).mappings().all()

        owner = db.session.execute(db.text(
            "SELECT DISTINCT user_id FROM budget.accounts ORDER BY user_id"
        )).scalars().all()
        expense_id = ref_cache.txn_type_id(TxnTypeEnum.EXPENSE)

        cells: dict = defaultdict(lambda: {"same": 0, "differ": []})
        bases = {
            uid: derived_amount_basis(
                uid, BalanceContext.build(uid, as_of=date.today()).scenario_id,
            )
            for uid in owner
        }
        for rec in rows:
            txn = db.session.get(Transaction, rec["row_id"])
            basis = bases[txn.account.user_id]
            kind = ("expense" if txn.transaction_type_id == expense_id
                    else "income")
            key = (amount_rule(txn).value, kind)
            try:
                got = Decimal(
                    resolve_transaction_amount(txn, basis),
                ).quantize(Decimal("0.01"))
            except Exception as exc:  # a refusal is a RESULT here, not a crash
                cells[key]["differ"].append(
                    (rec["row_id"], rec["name"], rec["old_plan"],
                     f"REFUSED {type(exc).__name__}"),
                )
                continue
            want = (Decimal(rec["old_plan"]).quantize(Decimal("0.01"))
                    if rec["old_plan"] is not None else None)
            if want is not None and got == want:
                cells[key]["same"] += 1
            else:
                cells[key]["differ"].append(
                    (rec["row_id"], rec["name"], str(want), str(got)),
                )

        print(f"settled declared rows: {len(rows)}\n")
        print(f"{'rule':<14}{'type':<10}{'reproduces':>11}{'differs':>9}")
        for key in sorted(cells):
            cell = cells[key]
            print(f"{key[0]:<14}{key[1]:<10}{cell['same']:>11}"
                  f"{len(cell['differ']):>9}")
        exp_same = sum(c["same"] for k, c in cells.items() if k[1] == "expense")
        exp_diff = sum(len(c["differ"]) for k, c in cells.items()
                       if k[1] == "expense")
        print(f"\nEXPENSE (what the spending report reads): "
              f"{exp_same} reproduce / {exp_same + exp_diff} total")
        for key in sorted(cells):
            for bad in cells[key]["differ"]:
                print(f"  DIFFERS {key}: txn {bad[0]} {bad[1]!r} "
                      f"stored {bad[2]} -> {bad[3]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
