"""The category breakdown: its amounts, shares and order, the window's filter, a share as a slice
of what moved, and a refund that reduces spend rather than adding to it.
"""

from datetime import date
from decimal import Decimal

from app import ref_cache
from app.enums import StatusEnum
from app.models.category import Category
from app.services import match_withdrawal, movement_removal, status_seam
from app.services.row_valuation import settled_contribution
from app.services.spending_report_service import SpendingWindow, compute_spending_report
from app.services.spending_report_service._breakdown import _build_breakdown, _totals_by_category
from app.services.spending_report_service._types import _CategoryTotal
from app.services.spending_report_service._window import _spent_total
from tests._test_helpers import add_entry, create_envelope_txn, settlement_if_settling

from ._helpers import _txn, _pp_window, _group


# ── Breakdown ────────────────────────────────────────────────────────


class TestBreakdown:
    """Category breakdown: amounts, shares, ordering, window filtering."""

    def test_shares_sum_to_one(self, app, seed_user, seed_periods, db):
        """Group/item shares are amount/total and sum to 1.0000.

        Window = period[0] with Rent 1200 (Home), Groceries 500 (Family),
        Car Payment 300 (Auto): total 2000.  Shares 0.60 / 0.25 / 0.15.
        """
        with app.app_context():
            _txn(db, seed_user, seed_periods[0], "Rent", "Rent", "1200.00")
            _txn(db, seed_user, seed_periods[0], "Food", "Groceries", "500.00")
            _txn(db, seed_user, seed_periods[0], "Car", "Car Payment", "300.00")
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[0]),
                user_settings=None,
            )
            assert report.hero.spent_total == Decimal("2000.00")
            # Ordered by amount descending.
            assert [g.group_name for g in report.breakdown] == [
                "Home", "Family", "Auto",
            ]
            home = _group(report, "Home")
            assert home.amount == Decimal("1200.00")
            assert home.share == Decimal("0.6")  # 1200/2000
            assert _group(report, "Family").share == Decimal("0.25")
            assert _group(report, "Auto").share == Decimal("0.15")
            # All group shares sum to exactly 1 (to the documented precision).
            share_sum = sum((g.share for g in report.breakdown), Decimal("0"))
            assert share_sum.quantize(Decimal("0.0001")) == Decimal("1.0000")
            # The single item under a group carries the same share.
            assert home.items[0].item_name == "Rent"
            assert home.items[0].share == Decimal("0.6")

    def test_multi_item_group_ordered(self, app, seed_user, seed_periods, db):
        """A group's items are amount-descending; the group sums them.

        Auto = Car Payment 300 + Gas 100 = 400; items ordered Car, Gas.
        """
        with app.app_context():
            gas = Category(
                user_id=seed_user["user"].id, group_name="Auto", item_name="Gas",
            )
            db.session.add(gas)
            db.session.flush()
            seed_user["categories"]["Gas"] = gas

            _txn(db, seed_user, seed_periods[0], "Car", "Car Payment", "300.00")
            _txn(db, seed_user, seed_periods[0], "Fuel", "Gas", "100.00")
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[0]),
                user_settings=None,
            )
            auto = _group(report, "Auto")
            assert auto.amount == Decimal("400.00")
            assert [i.item_name for i in auto.items] == ["Car Payment", "Gas"]
            assert auto.items[0].amount == Decimal("300.00")
            assert auto.items[1].amount == Decimal("100.00")

    def test_tied_items_in_one_group_order_by_name_not_by_insertion(
        self, app, seed_user, seed_periods, db,
    ):
        """Two categories that spent the SAME are ordered by the data (**P74**).

        The item list starts in the query's row order -- ``current_by_cat`` is
        a dict built by iterating it, and ``list.sort`` is stable -- so before
        this ruling a tie was decided by the plan.

        **The two are created in REVERSE alphabetical order on purpose**, so
        the insertion order and the correct order disagree: a rank keyed on
        ``amount`` alone leaves ``Zephyr`` first, and the total key puts
        ``Awning`` there.  ``item_name`` is unique within a group by the
        ``(user_id, group_name, item_name)`` constraint, so it totally orders
        this list while also reading alphabetically to a person.
        """
        with app.app_context():
            for name in ("Zephyr", "Awning"):
                cat = Category(
                    user_id=seed_user["user"].id,
                    group_name="Home", item_name=name,
                )
                db.session.add(cat)
                db.session.flush()
                seed_user["categories"][name] = cat
                _txn(
                    db, seed_user, seed_periods[0], f"T{name}", name, "75.00",
                )
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[0]),
                user_settings=None,
            )
            home = _group(report, "Home")
            tied = [
                i.item_name for i in home.items
                if i.amount == Decimal("75.00")
            ]
            assert tied == ["Awning", "Zephyr"], (
                "a tied item ranking must be a function of the data, not of "
                "the order the rows happened to be inserted in"
            )

    def test_window_filters_non_measured_rows(self, app, seed_user, seed_periods, db):
        """Only settled expenses in the window count.

        Seeds one settled expense (included) plus a projected, a credit, a
        cancelled, a settled income, a deleted expense, and a settled expense
        in a DIFFERENT period -- all excluded.  Spent total is the one row.

        The deleted expense is built as the delete door leaves it (ruling
        R-CC75): settled, its payment taken off through the one removal act,
        then hidden -- the database refuses a payment under a hidden row
        (R-CC89) and a row hidden holding one (R-CC92).  Rule-5
        re-expression, developer-confirmed 2026-09-23.
        """
        with app.app_context():
            _txn(db, seed_user, seed_periods[0], "Kept", "Rent", "500.00")
            _txn(db, seed_user, seed_periods[0], "Proj", "Rent", "999.00",
                 status_enum=StatusEnum.PROJECTED)
            _txn(db, seed_user, seed_periods[0], "Cred", "Rent", "999.00",
                 actual="999.00", status_enum=StatusEnum.CREDIT)
            _txn(db, seed_user, seed_periods[0], "Cxl", "Rent", "999.00",
                 status_enum=StatusEnum.CANCELLED)
            _txn(db, seed_user, seed_periods[0], "Inc", "Salary", "999.00",
                 actual="999.00", is_income=True, status_enum=StatusEnum.RECEIVED)
            deleted = _txn(db, seed_user, seed_periods[0], "Del", "Rent",
                           "999.00", actual="999.00")
            movement_removal.remove_movements(
                list(deleted.entries), seed_user["user"].id,
                because=match_withdrawal.LEFT_THE_BOOKS,
                press=None,
            )
            deleted.is_deleted = True
            _txn(db, seed_user, seed_periods[1], "Other", "Rent", "999.00")
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[0]),
                user_settings=None,
            )
            assert report.hero.spent_total == Decimal("500.00")
            assert len(report.breakdown) == 1
            assert report.breakdown[0].items[0].item_name == "Rent"

    def test_month_window_attribution(self, app, seed_user, seed_periods, db):
        """A month window keeps rows whose COALESCE(due_date, start) is in-month.

        seed_periods span Jan-May 2026.  A month=1 window keeps only the
        January-attributed settled expenses.
        """
        with app.app_context():
            # period[0] starts 2026-01-02 (January); period[4] starts in Feb+.
            _txn(db, seed_user, seed_periods[0], "JanRent", "Rent", "1200.00",
                 due_date=date(2026, 1, 10))
            _txn(db, seed_user, seed_periods[4], "FebRent", "Rent", "800.00",
                 due_date=date(2026, 2, 28))
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id,
                SpendingWindow(window_type="month", month=1, year=2026),
                user_settings=None,
            )
            assert report.hero.spent_total == Decimal("1200.00")
            assert report.scope.window_label == "January 2026"

    def test_cross_boundary_due_date_belongs_to_its_month(self, app, seed_user,
                                                          seed_periods, db):
        """A bill due in month M funded from a period outside M belongs to M.

        period[1] spans 2026-01-16..29 (entirely January) and holds a bill
        with due_date 2026-02-05.  The period never overlaps February, so
        the former period-overlap pre-filter attributed the row to NO month
        window (January excluded it by date; February never loaded its
        period).  The attribution-day fetch puts it where it is due:
        February counts the 150.00; January does not.
        """
        with app.app_context():
            _txn(db, seed_user, seed_periods[1], "EarlyFebBill", "Rent",
                 "150.00", due_date=date(2026, 2, 5))
            db.session.commit()

            february = compute_spending_report(
                seed_user["user"].id,
                SpendingWindow(window_type="month", month=2, year=2026),
                user_settings=None,
            )
            assert february.hero.spent_total == Decimal("150.00")
            assert february.breakdown[0].items[0].item_name == "Rent"

            january = compute_spending_report(
                seed_user["user"].id,
                SpendingWindow(window_type="month", month=1, year=2026),
                user_settings=None,
            )
            assert january.hero.spent_total == Decimal("0")


class TestARefundReducesSpendRatherThanAddingToIt:
    """A REFUND-dominated envelope must not report as SPENDING.

    Ruling **bank_import:R-II**, plan step ``bank_import:X-gj-2b``.  A merchant
    credit files as a NEGATIVE purchase against the envelope its merchant rule
    names, so a settled envelope's own figure -- ``sum(entries)`` on the
    ``purchases`` basis -- can be negative.

    **This class exists because an ``abs()`` was hiding exactly that**, at
    ``_breakdown._totals_by_category`` and ``_window._spent_total``.  Both were
    a provable no-op while ``ck_transaction_entries_positive_amount`` said
    ``amount > 0``, and both became a SIGN FLIP when that CHECK moved.
    Measured before the fix: ``-86.67`` reported as ``+86.67``, a `$173.34`
    error on a window in which the account RECEIVED the money.

    **The three shapes are asserted together deliberately.**  Only the
    refund-DOMINATED one inverts under ``abs()``; a partly-refunded envelope
    nets correctly either way.  A case staging only the ordinary or the
    partly-refunded shape would pass with the defect restored, which is why the
    boundary is pinned rather than a single example.
    """

    def _settled_envelope(self, db, seed_user, period, name, amounts):
        """Settle an envelope worth exactly the entries handed to it."""
        txn = create_envelope_txn(
            seed_user, db.session, period, name, Decimal("0.00"),
        )
        for amount in amounts:
            add_entry(
                db.session, seed_user, txn, Decimal(amount),
                period.start_date,
            )
        status_seam.apply_status_change(
            txn, ref_cache.status_id(StatusEnum.DONE),
            settlement=settlement_if_settling(
                txn, ref_cache.status_id(StatusEnum.DONE),
            ),
        )
        db.session.flush()
        return txn

    def test_the_three_shapes_carry_their_own_signs(
        self, app, db, seed_user, seed_periods,
    ):
        """Ordinary, partly refunded, and refund dominated.

        Asserts the WINDOW total as well as the per-row figures, because the
        defect was in the summing and a per-row assertion alone would not have
        seen it.
        """
        with app.app_context():
            period = seed_periods[0]
            ordinary = self._settled_envelope(
                db, seed_user, period, "Ordinary", ["100.00"],
            )
            partly = self._settled_envelope(
                db, seed_user, period, "Partly refunded",
                ["100.00", "-30.00"],
            )
            dominated = self._settled_envelope(
                db, seed_user, period, "Refund dominated", ["-86.67"],
            )
            db.session.commit()

            assert settled_contribution(ordinary) == Decimal("100.00")
            assert settled_contribution(partly) == Decimal("70.00")
            # The one the ``abs()`` inverted.
            assert settled_contribution(dominated) == Decimal("-86.67")

            # 100.00 + 70.00 - 86.67.  Under the defect this read 256.67.
            assert _spent_total(
                [ordinary, partly, dominated],
            ) == Decimal("83.33")

    def test_a_refunded_category_total_is_NEGATIVE(
        self, app, db, seed_user, seed_periods,
    ):
        """The per-category figure a refund-dominated window reports.

        A refund REDUCES what a category cost, which is what *did I stay in
        budget* has to mean.  Under the ``abs()`` this read ``+86.67`` and the
        category appeared as the window's largest spend.
        """
        with app.app_context():
            period = seed_periods[0]
            dominated = self._settled_envelope(
                db, seed_user, period, "Refund dominated", ["-86.67"],
            )
            db.session.commit()

            totals = _totals_by_category([dominated])

            assert totals[dominated.category_id].amount == Decimal("-86.67")


class TestTheShareIsASliceOfWhatMoved:
    """The "Where It Went" percent divides by MAGNITUDES, not by the net.

    Developer ruling 2026-09-01, plan step ``bank_import:X-gj-2b-3``.  Ruling
    **bank_import:R-II** let a category's refunds carry its window total below
    zero, and ``_share``'s denominator was the window's NET -- which refunds
    pull toward zero while a numerator does not.  Measured before the fix on
    ``Groceries 600.00`` beside ``Electronics -500.00``: a net of ``100.00``,
    so ``analytics/_spending.html`` printed **600%** and **-500%** beside
    figures of ``$600.00`` and ``-$500.00``.  The bars did not show it -- they
    scale to the largest row and floor at zero -- so the picture read correctly
    over numbers that did not.

    ``_share_base`` sums the categories' MAGNITUDES, which bounds every share
    in ``[-1, 1]`` outright.  These cases are built from ``_CategoryTotal``
    maps rather than from rows, because the defect is in the REDUCTION: a
    database-backed case would grade the query beside it and could pass on a
    window the query happened not to produce.
    """

    def _totals(self, *triples):
        """Return a ``{category_id: _CategoryTotal}`` map from label triples.

        Ids start at 1 so none of them is ``0``, which is the Uncategorized
        bucket and carries its own labelling rule.
        """
        return {
            index: _CategoryTotal(
                group_name=group, item_name=item, amount=Decimal(amount),
            )
            for index, (group, item, amount) in enumerate(triples, start=1)
        }

    def test_a_refunded_category_cannot_push_a_share_past_one(self):
        """600.00 beside -500.00: 55% and -45%, never 600% and -500%.

        The denominator is ``600 + 500 = 1100``, so the shares are
        ``600/1100 = 0.5455`` and ``-500/1100 = -0.4545`` (hand-computed, and
        quantized here so the assertion does not restate the producer's own
        division).  Restoring the net denominator makes them ``6`` and ``-5``
        and fails all four assertions.
        """
        rows = _build_breakdown(
            self._totals(
                ("Food", "Groceries", "600.00"),
                ("Tech", "Electronics", "-500.00"),
            ),
            {},
        )
        by_group = {row.group_name: row for row in rows}
        cents = Decimal("0.0001")

        assert by_group["Food"].share.quantize(cents) == Decimal("0.5455")
        assert by_group["Tech"].share.quantize(cents) == Decimal("-0.4545")
        # The item shares divide by the same base, so the drill-down agrees
        # with the group line above it.
        assert by_group["Food"].items[0].share.quantize(cents) == Decimal(
            "0.5455",
        )
        assert by_group["Tech"].items[0].share.quantize(cents) == Decimal(
            "-0.4545",
        )

    def test_a_MIXED_SIGN_group_divides_by_the_same_base_as_its_items(self):
        """The case the bound's own argument turns on, and none staged it.

        Plan step ``bank_import:X-gj-2b-3``, found by adversarial test-quality
        review.  Every other case here gives each category its own group, so
        ``group_amount == items[0].amount`` and the group-level ``_share`` is
        never distinguished from the item-level one -- the four assertions
        above are two numbers asserted twice.

        A mixed-sign group is where ``|group| < sum of its items' magnitudes``
        is a STRICT inequality, which is exactly what makes the ``[-1, 1]``
        bound hold for groups: ``Food`` sums to ``100.00`` while holding
        ``600.00`` and ``-500.00``, so its share is ``100/1100`` and its items
        are ``600/1100`` and ``-500/1100``.  Nothing here would notice if
        ``_group_row`` divided by something else.
        """
        rows = _build_breakdown(
            self._totals(
                ("Food", "Groceries", "600.00"),
                ("Food", "Electronics", "-500.00"),
            ),
            {},
        )
        cents = Decimal("0.0001")

        assert len(rows) == 1, "both categories belong to ONE group"
        assert rows[0].amount == Decimal("100.00")
        assert rows[0].share.quantize(cents) == Decimal("0.0909")
        by_item = {item.item_name: item for item in rows[0].items}
        assert by_item["Groceries"].share.quantize(cents) == Decimal("0.5455")
        assert by_item["Electronics"].share.quantize(cents) == Decimal("-0.4545")
        # THE BOUND, over every row this window renders.
        assert all(
            abs(share) <= 1
            for share in [rows[0].share] + [i.share for i in rows[0].items]
        )

    def test_a_window_with_no_refunds_is_unchanged(self):
        """The control the ruling turned on: magnitudes ARE the net there.

        ``Groceries 600.00`` and ``Dining 400.00`` sum to ``1000.00`` either
        way, so an ordinary month renders exactly what it always did.  Without
        this case the class above would pass on an implementation that divided
        by anything at all, including a constant.
        """
        rows = _build_breakdown(
            self._totals(
                ("Food", "Groceries", "600.00"),
                ("Fun", "Dining", "400.00"),
            ),
            {},
        )
        by_group = {row.group_name: row for row in rows}

        assert by_group["Food"].share == Decimal("0.6")
        assert by_group["Fun"].share == Decimal("0.4")

    def test_a_window_that_nets_to_zero_still_states_its_shares(self):
        """``600.00`` against ``-600.00``: a net of ZERO, and real shares.

        The old guard returned ``0`` for every share of a non-positive total,
        so this whole window rendered ``0%`` on both rows.  The magnitude base
        is ``1200.00`` and neither row is zero, which is the honest reading:
        half of what moved went out and half came back.
        """
        rows = _build_breakdown(
            self._totals(
                ("Food", "Groceries", "600.00"),
                ("Tech", "Electronics", "-600.00"),
            ),
            {},
        )
        by_group = {row.group_name: row for row in rows}

        assert by_group["Food"].share == Decimal("0.5")
        assert by_group["Tech"].share == Decimal("-0.5")

    def test_a_window_that_moved_nothing_has_no_shares(self):
        """The only base a sum of magnitudes can take that has no shares.

        A category present and worth exactly ``0.00`` -- an envelope settled
        with no purchases -- is the reachable shape, and the division is
        skipped rather than raising.
        """
        rows = _build_breakdown(
            self._totals(("Food", "Groceries", "0.00")), {},
        )

        assert rows[0].share == Decimal("0")
        assert rows[0].items[0].share == Decimal("0")
