"""A projected envelope expense's entry-aware reservation, its entries pre-loaded or not."""

from decimal import Decimal

from app.services.cash_ledger._amounts import _entry_aware_amount
from tests._repo_root import REPO_ROOT
from tests._test_helpers import add_txn

from ._helpers import _basis, _envelope, _held_back


class TestTheEntryAwareReservation:
    """The reservation for a still-Projected envelope expense, in its two halves.

    The six scope-doc scenarios (Section 4.2) plus the boundary shapes.  Every
    figure was the three-bucket reservation itself until plan step
    ``balance:X-bi-4a``; since ruling **R-BAL77** each case asserts the
    UNSPENT budget (:func:`_entry_aware_amount`), the IN-FLIGHT purchases
    (the plan tier's own item per un-dated purchase) and their sum, which is
    the old figure: ``max(E - P - C, U) == U + max(E - SUM(entries), 0)``.
    Before X-c2c2a each was asserted as ``5000.00 - reservation`` through the
    balance walk.
    """

    def test_no_entries_holds_the_full_estimate(
        self, app, db, seed_user, seed_periods,
    ):
        """Scenario 1: a tracked expense with no entries holds its estimate.

        est=500, debit=0, credit=0 -> the empty-entries short circuit returns
        ``effective_amount``, which for an unfilled Projected expense is the
        estimate: 500.00.  (Was: 5000 - 500 = 4500.)
        """
        with app.app_context():
            txn = _envelope(db.session, seed_user, seed_periods[1], "500.00")

            assert _entry_aware_amount(txn, _basis(txn)) == Decimal("500.00")

    def test_debit_under_budget_holds_the_full_reservation(
        self, app, db, seed_user, seed_periods,
    ):
        """Scenario 2: an uncleared debit under budget does not reduce it.

        est=500, uncleared_debit=200, credit=0.
        max(500 - 0 - 0, 200) = max(500, 200) = 500.  (Was: 4500.)
        """
        with app.app_context():
            txn = _envelope(
                db.session, seed_user, seed_periods[1], "500.00",
                [("200.00", False, None)],
            )

            assert _held_back(txn) == (
                Decimal("300.00"), Decimal("200.00"), Decimal("500.00"),
            )

    def test_a_credit_entry_reduces_the_reservation(
        self, app, db, seed_user, seed_periods,
    ):
        """Scenario 3: mixed debit + credit under budget -- credit reduces.

        est=500, uncleared_debit=300, credit=100.
        max(500 - 0 - 100, 300) = max(400, 300) = 400.  (Was: 4600.)
        """
        with app.app_context():
            txn = _envelope(
                db.session, seed_user, seed_periods[1], "500.00",
                [("300.00", False, None), ("100.00", True, None)],
            )

            assert _held_back(txn) == (
                Decimal("100.00"), Decimal("300.00"), Decimal("400.00"),
            )

    def test_all_credit_leaves_only_the_uncovered_portion(
        self, app, db, seed_user, seed_periods,
    ):
        """Scenario 4: all-credit entries -- only the uncovered part hits cash.

        est=500, debit=0, credit=400.
        max(500 - 0 - 400, 0) = max(100, 0) = 100.  (Was: 4900.)
        """
        with app.app_context():
            txn = _envelope(
                db.session, seed_user, seed_periods[1], "500.00",
                [("400.00", True, None)],
            )

            assert _entry_aware_amount(txn, _basis(txn)) == Decimal("100.00")

    def test_debit_overspend_raises_the_reservation_to_the_debits(
        self, app, db, seed_user, seed_periods,
    ):
        """Scenario 5: overspend -- the uncleared debit total is the floor.

        est=500, uncleared_debit=530, credit=0.
        max(500 - 0 - 0, 530) = max(500, 530) = 530.  (Was: 4470.)
        """
        with app.app_context():
            txn = _envelope(
                db.session, seed_user, seed_periods[1], "500.00",
                [("530.00", False, None)],
            )

            assert _held_back(txn) == (
                Decimal("0.00"), Decimal("530.00"), Decimal("530.00"),
            )

    def test_mixed_overspend_takes_the_debit_floor_over_the_reduction(
        self, app, db, seed_user, seed_periods,
    ):
        """Scenario 6: the debit floor beats the credit-reduced reservation.

        est=500, uncleared_debit=400, credit=200.
        max(500 - 0 - 200, 400) = max(300, 400) = 400.  (Was: 4600.)
        """
        with app.app_context():
            txn = _envelope(
                db.session, seed_user, seed_periods[1], "500.00",
                [("400.00", False, None), ("200.00", True, None)],
            )

            assert _held_back(txn) == (
                Decimal("0.00"), Decimal("400.00"), Decimal("400.00"),
            )

    def test_zero_estimate_with_a_debit_reserves_the_debit(
        self, app, db, seed_user, seed_periods,
    ):
        """A zero-budget envelope still holds back what was actually spent.

        est=0, uncleared_debit=50, credit=0.
        max(0 - 0 - 0, 50) = 50.  (Was: 4950.)
        """
        with app.app_context():
            txn = _envelope(
                db.session, seed_user, seed_periods[1], "0.00",
                [("50.00", False, None)],
            )

            assert _held_back(txn) == (
                Decimal("0.00"), Decimal("50.00"), Decimal("50.00"),
            )

    def test_credit_exceeding_the_estimate_floors_at_the_debits(
        self, app, db, seed_user, seed_periods,
    ):
        """Credit beyond the budget cannot drive the reservation negative.

        est=500, uncleared_debit=100, credit=600.
        max(500 - 0 - 600, 100) = max(-100, 100) = 100.  (Was: 4900.)
        """
        with app.app_context():
            txn = _envelope(
                db.session, seed_user, seed_periods[1], "500.00",
                [("100.00", False, None), ("600.00", True, None)],
            )

            assert _held_back(txn) == (
                Decimal("0.00"), Decimal("100.00"), Decimal("100.00"),
            )

    def test_one_cent_debit_does_not_disturb_the_reservation(
        self, app, db, seed_user, seed_periods,
    ):
        """The smallest representable entry: a one-cent uncleared debit.

        est=500, uncleared_debit=0.01, credit=0.
        max(500 - 0 - 0, 0.01) = 500.  (Was: 4500.)
        """
        with app.app_context():
            txn = _envelope(
                db.session, seed_user, seed_periods[1], "500.00",
                [("0.01", False, None)],
            )

            assert _held_back(txn) == (
                Decimal("499.99"), Decimal("0.01"), Decimal("500.00"),
            )

    def test_values_near_the_column_limit_do_not_overflow(
        self, app, db, seed_user, seed_periods,
    ):
        """Values at the ``Numeric(12,2)`` ceiling survive the ``max()``.

        est=9999999999.99, uncleared_debit=9999999999.99, credit=0.
        max(9999999999.99 - 0 - 0, 9999999999.99) = 9999999999.99.
        (Was: 10000000000.00 - 9999999999.99 = 0.01.)
        """
        with app.app_context():
            large = "9999999999.99"
            txn = _envelope(
                db.session, seed_user, seed_periods[1], large,
                [(large, False, None)],
            )

            assert _held_back(txn) == (
                Decimal("0.00"), Decimal(large), Decimal(large),
            )

    def test_a_row_with_no_template_is_worth_its_effective_amount(
        self, app, db, seed_user, seed_periods,
    ):
        """A plain (non-envelope) expense carries no entries, so no reduction.

        The pre-entries behaviour, and still the common case: no template
        means no entries, so the short circuit returns ``effective_amount``
        -- 1200.00.  (Was: 5000 - 1200 = 3800.)
        """
        with app.app_context():
            txn = add_txn(
                db.session, seed_user, seed_periods[1], "Rent", "1200.00",
                category_key="Rent",
            )
            db.session.commit()

            assert _entry_aware_amount(txn, _basis(txn)) == Decimal("1200.00")


class TestTheEntriesRelationshipIsNotASeam:
    """The reduction applies whether or not the caller pre-loaded entries.

    The structural fix for CRIT-01 / F-009 / E-25, and the reason
    :func:`_envelope` above does not re-query with ``selectinload``: symptom #1
    ($160 on the grid against $114.29 on ``/savings`` for one row) was exactly
    this seam, where the rule returned ``effective_amount`` whenever the
    consuming query had not issued the eager load.  The rule now reads
    ``txn.entries`` through the relationship descriptor, which lazy-loads on
    demand, so the value is a function of the DATA and not of the caller's
    query plan.
    """

    def test_an_expired_instance_still_reduces(
        self, app, db, seed_user, seed_periods,
    ):
        """Entries NOT resident on the instance -- the descriptor loads them.

        est=500, uncleared_debit=0, credit=300.
        max(500 - 0 - 300, 0) = 200.  (Was: 5000 - 200 = 4800.)

        The instance is expired first so ``entries`` is genuinely absent from
        its ``__dict__``, which is the state a caller that skipped the eager
        load produces.  Pre-E-25 that state returned 500.00.
        """
        with app.app_context():
            txn = _envelope(
                db.session, seed_user, seed_periods[1], "500.00",
                [("300.00", True, None)],
            )
            db.session.expire(txn)
            assert "entries" not in txn.__dict__

            assert _entry_aware_amount(txn, _basis(txn)) == Decimal("200.00")

    def test_the_silent_degrade_seam_is_absent_from_source(self):
        """C5-8: the ``'entries' not in __dict__`` short-circuit is not in source.

        The static half of the guard above: the test beside it proves the rule
        BEHAVES correctly today, and this one proves the shape that broke it
        cannot be re-introduced anywhere a balance is folded.  A future
        regression re-adding the eager-load presence check fails this loud.

        **It MOVED here at plan step X-g4b, and its scan WIDENED with the
        move.**  It lived in ``test_balance_resolver.py`` and scanned
        ``balance_at/_cash_engine.py``, ``balance_at/_calculator.py`` and the
        whole ``cash_ledger`` package; the first two were deleted at that step
        and the guard would have raised on their paths.  Its own rationale is
        why the replacement is a WIDENING rather than a subtraction: it enumerates
        a PACKAGE rather than naming files precisely because "a scan keyed on a
        file NAME would have gone quiet at exactly the moment the code it guards
        moved" (finding N-28, the shape where creating a module escapes a
        module-keyed gate).  Every balance producer now lives in ``balance_at``,
        so that package is scanned whole beside ``cash_ledger`` -- and
        ``rglob``, not ``glob``, so a future nested subpackage cannot escape
        either.

        Verified free at the move: neither forbidden pattern appears in either
        package on this date, so the widening costs nothing and closes the
        larger surface.
        """
        services = REPO_ROOT / "app" / "services"
        packages = {
            name: sorted((services / name).rglob("*.py"))
            for name in ("cash_ledger", "balance_at")
        }
        # The walk must really have happened AND must have reached the module
        # the forbidden patterns would actually appear in.  Asserting a total
        # instead would be a magic count that breaks when a package legitimately
        # gains or sheds a module.
        for name, sources in packages.items():
            assert sources, f"the {name} package enumerated to nothing"
        assert any(
            source.name == "_amounts.py" for source in packages["cash_ledger"]
        ), (
            "the entry-aware rule's module was not scanned: "
            f"{[s.name for s in packages['cash_ledger']]}"
        )
        assert any(
            source.name == "_cash_fold.py" for source in packages["balance_at"]
        ), (
            "the cash fold's module was not scanned: "
            f"{[s.name for s in packages['balance_at']]}"
        )

        forbidden_patterns = ("not in txn.__dict__", "'entries' not in")
        for sources in packages.values():
            for source_path in sources:
                source = source_path.read_text(encoding="utf-8")
                for pattern in forbidden_patterns:
                    assert pattern not in source, (
                        f"Forbidden seam pattern {pattern!r} found in "
                        f"{source_path}.  E-25 / CRIT-01 / F-009 regression: no "
                        "balance path may consult the instance __dict__ to "
                        "decide whether the entries-aware reduction applies."
                    )
