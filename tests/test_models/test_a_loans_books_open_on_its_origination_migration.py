"""Revision ``e5b2c8a41f73``: a set-up loan's books open on its origination.

Card plan#2436, task plan#3960, ruling **plan#3954**.  The revision installs
the loan-origination arm (:data:`app.opening_infrastructure.LOAN_ORIGINATION_ARM`)
over a census and withdraws it over another.  What the arm DOES to a write is
graded in ``tests/test_services/test_books_boundary/test_a_loan_opens_on_its_origination.py``;
what the builders emit for it, in ``test_books_boundary_arms.py`` beside this.
This file grades the revision: what it calls, and both of its refusals, each
run through the revision's own helpers against the real schema.
"""

from __future__ import annotations

import ast
import pathlib
from datetime import date
from decimal import Decimal

import pytest
import sqlalchemy as sa

from app import ref_cache
from app.enums import AccountOpeningSourceEnum
from app.models.account_opening import AccountOpening
from app.opening_infrastructure import (
    LOAN_ORIGINATION_ARM,
    apply_opening_functions,
    apply_opening_infrastructure,
)
from tests._test_helpers import (
    create_loan_account,
    create_settled_transfer,
    load_migration_module,
)

_MIGRATIONS = (
    pathlib.Path(__file__).resolve().parents[2] / "migrations" / "versions"
)

#: The revision under test.
_REVISION_FILE = "e5b2c8a41f73_a_set_up_loans_books_open_on_its_origination.py"

_REVISION = load_migration_module(_REVISION_FILE)

# The revision's arm sets and census helpers, each bound once.  They are
# private to the revision's module by design, and grading the revision through
# its own helpers rather than a copy of them is this file's point.

# Pylint: protected-access -- the revision's own arm set, graded directly.
_ARMS_BEFORE = _REVISION._ARMS_BEFORE  # pylint: disable=protected-access
# Pylint: protected-access -- the revision's own arm set, graded directly.
_ARMS_AFTER = _REVISION._ARMS_AFTER  # pylint: disable=protected-access
# Pylint: protected-access -- the revision's own census, run as it runs.
_REJECT_UPGRADE = _REVISION._reject_upgrade_over_violations  # pylint: disable=protected-access
# Pylint: protected-access -- the revision's own census, run as it runs.
_REJECT_DOWNGRADE = _REVISION._reject_downgrade_over_violations  # pylint: disable=protected-access


class TestTheLoanRevisionCensusesBothDirections:
    """``e5b2c8a41f73`` installs the loan-origination arm over a census, and
    withdraws it over one.

    The upgrade's census finds a movement or matched line on or before a
    configured loan's origination; the downgrade's finds one on or before a
    loan's governing opening record, a row the arm admitted and the restored
    day refuses.  Each is a control shown to fire and shown to stay quiet,
    run through the revision's own helpers against the real schema.
    """

    @staticmethod
    def _executor(db):
        """Return an executor running each statement on the test session.

        Args:
            db: The ``db`` fixture.

        Returns:
            A single-argument callable for the builders.
        """
        return lambda statement: db.session.execute(db.text(statement))

    def test_the_revision_inlines_no_function_body(self):
        """The arm-explicit builder is what the revision calls, not a copy."""
        tree = ast.parse((_MIGRATIONS / _REVISION_FILE).read_text())
        literals = [
            node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        ]

        for text in literals:
            assert "LANGUAGE plpgsql" not in text
            assert "LANGUAGE sql" not in text

    def test_its_downgrade_leaves_the_matched_line_revisions_arms(self):
        """Withdrawing the loan arm restores ``d1f6a83c9e47``'s boundary."""
        previous = load_migration_module(
            "d1f6a83c9e47_a_matched_bank_line_cannot_predate_the_books.py"
        )

        # Pylint: protected-access -- the previous revision's own arm set,
        # which this one's downgrade must restore.
        previous_arms = previous._ARMS_AFTER  # pylint: disable=protected-access

        assert _ARMS_BEFORE == previous_arms
        assert _ARMS_AFTER == (*previous_arms, LOAN_ORIGINATION_ARM)

    def test_a_clean_database_passes_both_censuses(
        self, app, db, seed_user, seed_periods,
    ):
        """Both censuses run their real SQL against the real schema.

        A loan whose payment settles after its origination and after its
        opening record is legal under either day, so neither direction
        refuses it.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Clean Loan",
                origination_date=date(2026, 1, 1),
            )
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[5], amount=Decimal("50.00"),
                settled_on=date(2026, 3, 13),
            )
            db.session.commit()
            connection = db.session.connection()

            apply_opening_functions(
                self._executor(db), arms=_ARMS_AFTER,
            )
            assert _REJECT_UPGRADE(
                connection,
            ) is None
            apply_opening_functions(
                self._executor(db), arms=_ARMS_BEFORE,
            )
            assert _REJECT_DOWNGRADE(
                connection,
            ) is None
            db.session.rollback()

    def test_a_payment_before_the_origination_stops_the_upgrade(
        self, app, db, seed_user, seed_periods,
    ):
        """FIRING CONTROL: the state ruling plan#3954 makes illegal.

        A mortgage closing 2026-04-15 with an installment settled 2026-03-10,
        built with the arm withdrawn, which is the state a database below
        ``e5b2c8a41f73`` can hold.  The refusal names the loan, the day its
        books would open and the earliest payment in the way.

        **Built past the service**, since plan task plan#3964 made the
        transfer writer refuse a payment before a loan's start itself, as a
        door below this revision did not.  The loan is set up originating
        2026-01-01, so the payment settles through the writer, and the
        origination then moves onto 2026-04-15 by a raw statement, which the
        withdrawn arm does not grade.  It settled through the writer onto a
        loan already closing in April until that task.
        """
        with app.app_context():
            apply_opening_infrastructure(
                self._executor(db), arms=_ARMS_BEFORE,
            )
            db.session.commit()
            loan = create_loan_account(
                seed_user, db.session, name="Closing In April",
                principal=Decimal("200000.00"), term=360,
                origination_date=date(2026, 1, 1),
            )
            # periods[8] starts 2026-04-24: the installment is after the
            # origination either way, while the money moves on 2026-03-10.
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[8], amount=Decimal("1200.00"),
                settled_on=date(2026, 3, 10),
            )
            db.session.execute(sa.text(
                "UPDATE budget.loan_params SET origination_date = :d "
                "WHERE account_id = :a"
            ), {"d": date(2026, 4, 15), "a": loan.id})
            db.session.commit()
            loan_id = loan.id

            apply_opening_functions(
                self._executor(db), arms=_ARMS_AFTER,
            )
            with pytest.raises(RuntimeError) as caught:
                _REJECT_UPGRADE(
                    db.session.connection(),
                )
            db.session.rollback()

        message = str(caught.value)
        assert "cannot open a configured loan's books" in message
        assert f"account {loan_id} (Closing In April)" in message
        assert "books open 2026-04-15" in message
        assert "earliest movement 2026-03-10" in message

    def test_a_payment_on_the_opening_record_stops_the_downgrade(
        self, app, db, seed_user, seed_periods,
    ):
        """FIRING CONTROL: the state the arm admits and the old day refuses.

        Line 172's shape: a loan originated long before its opening record,
        with a payment ON that record's day.  Legal under the arm, so the
        head database accepts it; withdrawing the arm over it would leave a
        constraint the next write aborts on.
        """
        with app.app_context():
            loan = create_loan_account(
                seed_user, db.session, name="Matched On Its Record",
                origination_date=date(2026, 1, 1),
            )
            paid_on = date(2026, 3, 13)
            create_settled_transfer(
                seed_user, db.session, seed_user["account"], loan,
                seed_periods[5], amount=Decimal("50.00"), settled_on=paid_on,
            )
            db.session.commit()
            # The loan's opening record moves ONTO the payment's day.  Legal
            # under the arm: the loan's books open on its origination.
            db.session.add(AccountOpening(
                account_id=loan.id,
                opened_on=paid_on,
                opening_equity=Decimal("0.00"),
                source_id=ref_cache.account_opening_source_id(
                    AccountOpeningSourceEnum.USER_DECLARED,
                ),
            ))
            db.session.commit()
            loan_id = loan.id

            apply_opening_functions(
                self._executor(db), arms=_ARMS_BEFORE,
            )
            with pytest.raises(RuntimeError) as caught:
                _REJECT_DOWNGRADE(
                    db.session.connection(),
                )
            db.session.rollback()

        message = str(caught.value)
        assert "e5b2c8a41f73 cannot be downgraded" in message
        assert f"account {loan_id} (Matched On Its Record)" in message
        assert f"books open {paid_on.isoformat()}" in message
        assert f"earliest movement {paid_on.isoformat()}" in message

    def test_neither_refusal_hands_over_a_query_its_rollback_changes(self):
        """FIRING CONTROL for a diagnostic answered by the wrong body.

        Each refusal rolls back its own ``CREATE OR REPLACE`` of the books day,
        so a query calling ``budget.account_books_opened_on`` would be answered
        by the OTHER direction's body.  Each spells its rule out instead.
        """
        source = (_MIGRATIONS / _REVISION_FILE).read_text()
        for helper, rule in (
            ("_reject_upgrade_over_violations",
             "m.settled_on <= p.origination_date"),
            ("_reject_downgrade_over_violations",
             "budget.account_openings o WHERE o.account_id = m.account_id"),
        ):
            body = source[source.index(f"def {helper}("):]
            message_text = body[
                body.index("raise RuntimeError("):body.index("\n    )")
            ]
            assert "account_books_opened_on" not in message_text, helper
            assert "books_hold" not in message_text, helper
            assert rule in message_text, helper
