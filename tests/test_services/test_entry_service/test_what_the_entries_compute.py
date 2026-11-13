"""What a row's entries compute: their sums, the remaining figure, the purchases total and the
percent complete.
"""

from decimal import Decimal

from app.services import entry_service
from app.services.row_valuation import purchases_total

from ._helpers import _make_entry


# ── Computation Tests ──────────────────────────────���──────────────


class TestComputeEntrySums:
    """Tests for entry_service.compute_entry_sums()."""

    def test_compute_entry_sums_all_debit(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Sum debit entries: (180, 0).

        $50 + $100 + $30 = $180 debit, $0 credit.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entries = [
                _make_entry(txn, user, amount="50.00"),
                _make_entry(txn, user, amount="100.00"),
                _make_entry(txn, user, amount="30.00"),
            ]

            sum_debit, sum_credit = entry_service.compute_entry_sums(entries)

            assert sum_debit == Decimal("180.00")
            assert sum_credit == Decimal("0")

    def test_compute_entry_sums_mixed(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Mixed entries: (150, 80).

        Debit: $100 + $50 = $150.  Credit: $80.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entries = [
                _make_entry(txn, user, amount="100.00"),
                _make_entry(txn, user, amount="50.00"),
                _make_entry(txn, user, amount="80.00", is_credit=True),
            ]

            sum_debit, sum_credit = entry_service.compute_entry_sums(entries)

            assert sum_debit == Decimal("150.00")
            assert sum_credit == Decimal("80.00")

    def test_compute_entry_sums_empty(self, app):
        """Empty list returns (0, 0)."""
        with app.app_context():
            sum_debit, sum_credit = entry_service.compute_entry_sums([])

            assert sum_debit == Decimal("0")
            assert sum_credit == Decimal("0")

    def test_compute_entry_sums_single_debit(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Single debit entry: (50, 0)."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _make_entry(txn, seed_user["user"], amount="50.00")

            sum_debit, sum_credit = entry_service.compute_entry_sums([entry])

            assert sum_debit == Decimal("50.00")
            assert sum_credit == Decimal("0")

    def test_compute_entry_sums_single_credit(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Single credit entry: (0, 75)."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _make_entry(
                txn, seed_user["user"], amount="75.00", is_credit=True,
            )

            sum_debit, sum_credit = entry_service.compute_entry_sums([entry])

            assert sum_debit == Decimal("0")
            assert sum_credit == Decimal("75.00")

    def test_compute_entry_sums_all_credit(
        self, app, db, seed_user, seed_entry_template,
    ):
        """All credit entries: (0, 250).

        $100 + $150 = $250 credit, $0 debit.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entries = [
                _make_entry(txn, user, amount="100.00", is_credit=True),
                _make_entry(txn, user, amount="150.00", is_credit=True),
            ]

            sum_debit, sum_credit = entry_service.compute_entry_sums(entries)

            assert sum_debit == Decimal("0")
            assert sum_credit == Decimal("250.00")


class TestComputeRemaining:
    """Tests for entry_service.compute_remaining()."""

    def test_compute_remaining_under_budget(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Under budget: remaining = 500 - 330 = 170.

        Entries: $200 debit + $130 debit = $330 total.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entries = [
                _make_entry(txn, user, amount="200.00"),
                _make_entry(txn, user, amount="130.00"),
            ]

            remaining = entry_service.compute_remaining(
                Decimal("500.00"), entries,
            )

            assert remaining == Decimal("170.00")

    def test_compute_remaining_over_budget(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Over budget: remaining = 500 - 530 = -30.

        Negative remaining means overspent.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entries = [
                _make_entry(txn, user, amount="300.00"),
                _make_entry(txn, user, amount="230.00"),
            ]

            remaining = entry_service.compute_remaining(
                Decimal("500.00"), entries,
            )

            assert remaining == Decimal("-30.00")

    def test_compute_remaining_zero(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Exactly on budget: remaining = 500 - 500 = 0."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entries = [
                _make_entry(txn, user, amount="300.00"),
                _make_entry(txn, user, amount="200.00"),
            ]

            remaining = entry_service.compute_remaining(
                Decimal("500.00"), entries,
            )

            assert remaining == Decimal("0")

    def test_compute_remaining_with_credit_entries(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Credit entries count toward budget consumption.

        remaining = 500 - (200 debit + 100 credit) = 200.
        Payment method doesn't affect remaining budget.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entries = [
                _make_entry(txn, user, amount="200.00"),
                _make_entry(txn, user, amount="100.00", is_credit=True),
            ]

            remaining = entry_service.compute_remaining(
                Decimal("500.00"), entries,
            )

            assert remaining == Decimal("200.00")

    def test_compute_remaining_zero_estimated(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Estimated amount of 0 with entries: remaining goes negative.

        remaining = 0 - 50 = -50.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _make_entry(txn, seed_user["user"], amount="50.00")

            remaining = entry_service.compute_remaining(
                Decimal("0"), [entry],
            )

            assert remaining == Decimal("-50.00")

    def test_a_REFUND_carries_remaining_ABOVE_the_budget(
        self, app, db, seed_user, seed_entry_template,
    ):
        """`$100.00` budget, one `-$50.00` refund: remaining is `$150.00`.

        Developer ruling 2026-09-01, ruling **bank_import:R-II**, plan step
        ``bank_import:X-gj-2b-3``.  A merchant credit files as a NEGATIVE
        purchase, so this figure is UNBOUNDED ABOVE and the base is a NET cash
        target: `$150.00` of net spending may still be recorded against a
        `$100.00` plan, because `-$50.00` of it has already happened.

        Capping it at the budget was put to the developer with these numbers
        and refused -- it breaks ``sum(entries) + remaining == budget``, which
        every surface that renders this depends on, and it would make the
        dashboard disagree with the balance reservation beside it.  Asserted
        because a cap is the change somebody will reach for when they meet
        `$150.00` on a `$100.00` envelope and read it as a bug.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _make_entry(
                txn, seed_user["user"], amount="-50.00",
                description="Amazon refund",
            )

            remaining = entry_service.compute_remaining(
                Decimal("100.00"), [entry],
            )

            assert remaining == Decimal("150.00")
            # THE IDENTITY the figure exists inside.
            assert remaining + entry.amount == Decimal("100.00")

    def test_a_PARTLY_refunded_envelope_nets_below_its_budget(
        self, app, db, seed_user, seed_entry_template,
    ):
        """The control: `$80.00` spent and `$30.00` back is `$50.00` consumed.

        Without it the case above is satisfied by an implementation that
        ignores negative entries entirely, which would answer `$100.00` there
        and `$20.00` here.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]
            entries = [
                _make_entry(txn, user, amount="80.00"),
                _make_entry(
                    txn, user, amount="-30.00", description="Amazon refund",
                ),
            ]

            assert entry_service.compute_remaining(
                Decimal("100.00"), entries,
            ) == Decimal("50.00")

    def test_compute_remaining_empty_entries(self, app):
        """No entries: remaining equals the estimated amount."""
        with app.app_context():
            remaining = entry_service.compute_remaining(
                Decimal("500.00"), [],
            )

            assert remaining == Decimal("500.00")

    def test_compute_remaining_large_amounts(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Large amounts within Numeric(12,2) precision.

        remaining = 9999999999.99 - 9999999999.98 = 0.01.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _make_entry(
                txn, seed_user["user"], amount="9999999999.98",
            )

            remaining = entry_service.compute_remaining(
                Decimal("9999999999.99"), [entry],
            )

            assert remaining == Decimal("0.01")

    def test_compute_remaining_anchors_on_estimated_only(
        self, app, db, seed_user, seed_entry_template,
    ):
        """C30-3 (E-21 / MED-03 / F-028 / F-056): compute_remaining
        takes only ``estimated_amount`` and therefore cannot anchor on
        ``actual_amount`` or switch on status -- the structural
        guarantee that the entry-tracked bill row's remaining always
        shares the declared E-21 budget base with the row's amount
        cell.

        Worked example: estimated=$120 (the E-21 base), entries
        summing $80; ``actual_amount`` could be anything (e.g. $100)
        and the result MUST stay anchored on $120.
            remaining = 120.00 - 80.00 = 40.00
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]
            entries = [
                _make_entry(txn, user, amount="50.00"),
                _make_entry(txn, user, amount="30.00"),
            ]

            # Even if a hypothetical caller passed actual_amount, the
            # function would compute against that value -- the
            # signature accepts no actual or status, so anchoring on
            # estimated is structural.  Pass estimated and assert the
            # E-21 result.
            remaining = entry_service.compute_remaining(
                Decimal("120.00"), entries,
            )

            assert remaining == Decimal("40.00")

    def test_compute_remaining_signature_excludes_actual_and_status(self):
        """C30-3 partner: the function signature cannot consult
        ``actual_amount`` or ``status`` -- it accepts only the declared
        ``budget`` and ``entries`` -- so a future change
        cannot silently shift the entry-tracked row's base away from
        the E-21 / MED-03 base without an explicit signature
        change (which would surface in review).

        **The first parameter is named ``budget`` since plan step X-au-c2b**,
        where it was ``estimated_amount``.  That rename is the assertion's
        subject rather than incidental: a parameter named after a COLUMN
        invites the next caller to pass that column, and under the amount model
        a derived row's is ``NULL``.  The base is what the row's amount
        RESOLVES to, and the name says so.
        """
        # pylint: disable=import-outside-toplevel
        import inspect
        sig = inspect.signature(entry_service.compute_remaining)
        # E-21 base contract: the only inputs are the declared base and the
        # entries to subtract.  No txn, no status, no actual.
        assert list(sig.parameters) == ["budget", "entries"]


class TestPurchasesTotal:
    """Tests for ``row_valuation.purchases_total()``.

    It was ``entry_service.compute_actual_from_entries`` until plan step
    **X-au-c3**, which moved it to :mod:`app.services.row_valuation`: the
    settlement record's own accessor needs it, that module sits under both the
    cash and loan tiers, and the old name referred to a column the step removed.
    The tests stay here, beside the envelope behaviour they describe.
    """

    def test_purchases_total_includes_credit(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Actual = sum of all entries (debit + credit).

        $200 debit + $100 debit + $100 credit = $400.
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entries = [
                _make_entry(txn, user, amount="200.00"),
                _make_entry(txn, user, amount="100.00"),
                _make_entry(txn, user, amount="100.00", is_credit=True),
            ]

            actual = purchases_total(entries)
            assert actual == Decimal("400.00")

    def test_purchases_total_empty(self, app):
        """Empty entries: actual = 0."""
        with app.app_context():
            actual = purchases_total([])
            assert actual == Decimal("0")

    def test_purchases_total_single_entry(
        self, app, db, seed_user, seed_entry_template,
    ):
        """Single entry: actual equals that entry's amount."""
        with app.app_context():
            txn = seed_entry_template["transaction"]
            entry = _make_entry(txn, seed_user["user"], amount="42.50")

            actual = purchases_total([entry])
            assert actual == Decimal("42.50")

    def test_purchases_total_all_credit(
        self, app, db, seed_user, seed_entry_template,
    ):
        """All credit entries: actual still equals their sum.

        $80 + $120 = $200 (total spending regardless of payment method).
        """
        with app.app_context():
            txn = seed_entry_template["transaction"]
            user = seed_user["user"]

            entries = [
                _make_entry(txn, user, amount="80.00", is_credit=True),
                _make_entry(txn, user, amount="120.00", is_credit=True),
            ]

            actual = purchases_total(entries)
            assert actual == Decimal("200.00")


# ── pct_complete (Commit 9 / F-23) ────────────────────────────────


class TestPctComplete:
    """Tests for entry_service.pct_complete().

    Locks the MED-04 / E-16 standard: money math is service-layer
    Decimal, not route-layer float.  The companion route used to cast
    the entry-percentage to float before handing it to the template;
    the helper now keeps the result as a Decimal end-to-end, mirroring
    the shape of dashboard_service._safe_pct_complete.
    """

    def test_pct_complete_normal_case(self):
        """C9-1: total $50, target $100 -> Decimal("50.00").

        Hand arithmetic: 50 / 100 * 100 = 50, quantised to 2dp = 50.00.
        """
        assert entry_service.pct_complete(
            Decimal("50"), Decimal("100"),
        ) == Decimal("50.00")

    def test_pct_complete_clamps_at_100(self):
        """C9-2: over-budget total $150 against target $100 -> Decimal("100.00").

        Hand arithmetic: 150 / 100 * 100 = 150, clamped at 100 because
        the progress-bar width has no defined meaning past 100%.
        """
        assert entry_service.pct_complete(
            Decimal("150"), Decimal("100"),
        ) == Decimal("100.00")

    def test_pct_complete_target_zero_guard(self):
        """C9-3: target $0 -> Decimal("0") (no divide-by-zero).

        A zero target is a degenerate budget line; returning 0 (rather
        than raising) lets the companion view render the row without
        special-casing in the template.
        """
        assert entry_service.pct_complete(
            Decimal("50"), Decimal("0"),
        ) == Decimal("0")

    def test_pct_complete_target_negative_guard(self):
        """Negative target -> Decimal("0") (same guard path as zero).

        Defensive: ``estimated_amount`` is CHECK-constrained to >= 0
        at the storage tier, but the helper should not produce a
        misleading negative percentage if a future caller violates
        that invariant in an in-memory object.
        """
        assert entry_service.pct_complete(
            Decimal("50"), Decimal("-10"),
        ) == Decimal("0")

    def test_pct_complete_zero_total(self):
        """Zero spending against a positive target -> Decimal("0.00").

        Hand arithmetic: 0 / 100 * 100 = 0.00.
        """
        assert entry_service.pct_complete(
            Decimal("0"), Decimal("100"),
        ) == Decimal("0.00")

    def test_pct_complete_returns_decimal_not_float(self):
        """MED-04 / E-16: the return type is Decimal, not float.

        Locks the post-F-23 contract that no caller has to do a
        ``float(Decimal_expression)`` cast at the route layer.
        """
        result = entry_service.pct_complete(
            Decimal("55.50"), Decimal("100.00"),
        )
        assert isinstance(result, Decimal)
        # Hand arithmetic: 55.50 / 100 * 100 = 55.50.
        assert result == Decimal("55.50")
