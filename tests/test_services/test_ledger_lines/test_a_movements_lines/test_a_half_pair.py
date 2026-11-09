"""A transfer holding one side's record reads that half into transit, and the band rule refuses it.

The one case of the store writer's fail-loud tests that graded the read: the rest refused a
missing chart row, a ``None`` scenario or an unbalanced entry, and went with the writer at plan
step plan#2914.
"""

from __future__ import annotations

from decimal import Decimal

from app.extensions import db as _db
from app.services.ledger_lines import transit_key
from tests._ledger_reads import account_net, ledger_net
from tests._test_helpers import (
    create_settled_transfer,
    refused_by_database_rule,
)

from ._helpers import _scenario_id


class TestAHalfPair:
    """A pair with one side's record is the in-transit state."""

    def test_a_half_pair_reads_its_half_into_transit(
        self, app, db, seed_user, savings,
    ):
        """A transfer holding only its from-side record books that side, in transit.

        **A declared behaviour change of plan step ``balance:X-bi-6-3``
        (rulings R-BAL45, R-BAL101).**  The one-entry writer read the INCOME
        shadow's record for the pair and REFUSED when it was missing; shape C
        has no pair to read -- each side's movement books on its own, against
        transit -- so a pair with one side is the in-transit state: money left
        Checking and has not arrived.  The read neither refuses nor fabricates
        the missing side; the band rule polices the pair (below).

        Arithmetic: a $100.00 Checking -> Savings settle, then the to-side's
        record is removed: the from-side still books {Checking -100, transit
        +100} and the missing side nothing, so transit holds +100.00 -- the
        half in flight -- Checking sits at its $1000.00 opening less the
        $100.00, and Savings on its $100.00 opening.

        **The half pair is STAGED inside the save since plan step
        ``balance:X-bi-6-4d-2``** (ruling **R-BAL167** class 2): the to-side's
        record hangs off the TRANSFER since that step, and a settled transfer
        holding ONE side's record is refused at COMMIT (the band rule).  The
        record comes off by its side link, the read is asked inside the save,
        and the commit is then refused.
        """
        with app.app_context():
            checking = seed_user["account"]
            transfer = create_settled_transfer(
                seed_user, _db.session, checking, savings,
                seed_user["bootstrap_period"], amount=Decimal("100.00"),
            )
            _db.session.commit()
            assert ledger_net(
                _db.session, transit_key(), _scenario_id(seed_user),
            ) == Decimal("0.00")
            # Remove the to-side's record via raw SQL, around the service: a
            # record is no longer deleted with anything (R-CC54; rule-5
            # re-expression, developer-confirmed 2026-09-23), and it is the
            # TRANSFER's, by its side link, since plan step
            # balance:X-bi-6-4d-2.  STAGED: not committed.
            _db.session.execute(_db.text(
                "DELETE FROM budget.transaction_entries "
                "WHERE income_transfer_id = :t"
            ), {"t": transfer.id})
            _db.session.expire_all()

            assert ledger_net(
                _db.session, transit_key(), _scenario_id(seed_user),
            ) == Decimal("100.00")
            # 1000.00 - 100.00 = 900.00: the from-side still books.
            assert account_net(
                _db.session, checking.id, _scenario_id(seed_user),
            ) == Decimal("900.00")
            # The missing side books nothing: Savings sits on its opening.
            assert account_net(
                _db.session, savings.id, _scenario_id(seed_user),
            ) == Decimal("100.00")

            # The band rule refuses the half pair at the commit (ruling
            # R-BAL167 class 2).
            with refused_by_database_rule(
                "is settled but its sides hold 1 dated and 0 un-dated",
            ) as caught:
                _db.session.commit()
            _db.session.rollback()
            assert f"transfer {transfer.id} " in str(caught.value)
