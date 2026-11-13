"""Window-over-window deltas: items, groups and the By-change rows on the D7 change basis, and the
"new" badge, which asks presence.
"""

from decimal import Decimal

from app.models.category import Category
from app.models.transaction import Transaction
from app.services.cash_ledger import derived_amount_basis
from app.services.spending_report_service import compute_spending_report
from app.services.spending_report_service._surprises import _MAX_SURPRISES, _build_surprises
from app.services.spending_report_service._breakdown import _build_breakdown, _build_changes
from app.services.spending_report_service._types import _CategoryTotal

from ._helpers import _txn, _pp_window, _group


# ── Window-over-window deltas (breakdown + By-change rows) ──────────


class TestDeltas:
    """Item, group, and By-change deltas on the D7 change basis."""

    def test_item_and_group_deltas(self, app, seed_user, seed_periods, db):
        """Items and groups carry current minus prior; new categories flag.

        period[0] (prior): Rent 1000, Groceries 400.
        period[1] (viewed): Rent 900, Car Payment 250.
        Viewed breakdown: Home/Rent 900 (delta 900 - 1000 = -100, not new);
        Auto/Car Payment 250 (delta +250, is_new -- no prior spend).
        Groceries has no viewed row, so Family is absent from the breakdown.
        """
        with app.app_context():
            _txn(db, seed_user, seed_periods[0], "Rent0", "Rent", "1000.00")
            _txn(db, seed_user, seed_periods[0], "Food0", "Groceries", "400.00")
            _txn(db, seed_user, seed_periods[1], "Rent1", "Rent", "900.00")
            _txn(db, seed_user, seed_periods[1], "Car1", "Car Payment", "250.00")
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[1]),
                user_settings=None,
            )
            assert [g.group_name for g in report.breakdown] == ["Home", "Auto"]
            home = _group(report, "Home")
            assert home.delta == Decimal("-100.00")   # 900 - 1000
            assert home.is_new is False
            assert home.items[0].delta == Decimal("-100.00")
            assert home.items[0].is_new is False
            auto = _group(report, "Auto")
            assert auto.delta == Decimal("250.00")    # 250 - 0
            assert auto.is_new is True
            assert auto.items[0].is_new is True

    def test_group_delta_includes_stopped_categories(self, app, seed_user,
                                                     seed_periods, db):
        """A group's prior side sums categories with no current spend.

        Home has two categories: Rent and Electricity (created here).
        period[0] (prior): Rent 1000, Electricity 200 -> Home prior 1200.
        period[1] (viewed): Electricity 150 only -> Home current 150.
        Group delta = 150 - 1200 = -1050 (the stopped Rent still counts);
        the Electricity ITEM delta is only 150 - 200 = -50.
        """
        with app.app_context():
            electricity = Category(
                user_id=seed_user["user"].id, group_name="Home",
                item_name="Electricity",
            )
            db.session.add(electricity)
            db.session.flush()
            seed_user["categories"]["Electricity"] = electricity

            _txn(db, seed_user, seed_periods[0], "Rent0", "Rent", "1000.00")
            _txn(db, seed_user, seed_periods[0], "Elec0", "Electricity",
                 "200.00")
            _txn(db, seed_user, seed_periods[1], "Elec1", "Electricity",
                 "150.00")
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[1]),
                user_settings=None,
            )
            home = _group(report, "Home")
            assert home.amount == Decimal("150.00")
            assert home.delta == Decimal("-1050.00")  # 150 - (1000 + 200)
            assert home.items[0].delta == Decimal("-50.00")  # 150 - 200

    def test_changes_include_zero_current_rows_sorted(self, app, seed_user,
                                                      seed_periods, db):
        """Changes span both windows; stopped categories appear at $0.

        period[0] (prior): Rent 1000, Groceries 400.
        period[1] (viewed): Groceries 460, Car Payment 250.
        Rows by |delta| descending: Rent (0 - 1000 = -1000, the
        zero-current rider), Car Payment (+250, is_new), Groceries
        (460 - 400 = +60).
        """
        with app.app_context():
            _txn(db, seed_user, seed_periods[0], "Rent0", "Rent", "1000.00")
            _txn(db, seed_user, seed_periods[0], "Food0", "Groceries", "400.00")
            _txn(db, seed_user, seed_periods[1], "Food1", "Groceries", "460.00")
            _txn(db, seed_user, seed_periods[1], "Car1", "Car Payment", "250.00")
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[1]),
                user_settings=None,
            )
            rows = report.changes
            assert [r.item_name for r in rows] == [
                "Rent", "Car Payment", "Groceries",
            ]
            rent, car, groceries = rows
            assert (rent.current, rent.prior) == (Decimal("0"), Decimal("1000.00"))
            assert rent.delta == Decimal("-1000.00")
            assert rent.is_new is False              # nothing new about a stop
            assert rent.group_name == "Home"         # labels from the prior row
            assert car.delta == Decimal("250.00")
            assert car.is_new is True
            assert groceries.delta == Decimal("60.00")
            assert groceries.is_new is False

    def test_changes_tie_breaks_on_current_then_name(self, app, seed_user,
                                                     seed_periods, db):
        """Equal |delta| rows order by current spend, then item name.

        period[0]: Rent 100 (stops -> delta -100).  period[1]: Car Payment
        100 (new -> delta +100).  |delta| ties at 100; Car Payment has the
        higher current spend (100 > 0) so it sorts first.
        """
        with app.app_context():
            _txn(db, seed_user, seed_periods[0], "Rent0", "Rent", "100.00")
            _txn(db, seed_user, seed_periods[1], "Car1", "Car Payment", "100.00")
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[1]),
                user_settings=None,
            )
            assert [r.item_name for r in report.changes] == [
                "Car Payment", "Rent",
            ]

    def test_tied_groups_order_by_name_not_by_the_query_plan(
        self, app, seed_user, seed_periods, db,
    ):
        """Two groups that spent the SAME are ordered by the data (**P74**).

        Neither settled-expense query carries an ``ORDER BY``; the per-category
        totals land in a dict in whatever order the database returned them,
        ``items_by_group`` is built by iterating that dict, and ``list.sort``
        is stable -- so before this ruling a tie was broken by the query plan,
        and an edit to either SELECT list could silently reorder the screen.

        ``Home`` and ``Auto`` both spend ``$100.00``.  ``Auto`` sorts first
        because ``A`` precedes ``H``, not because of anything the planner did.
        """
        with app.app_context():
            _txn(db, seed_user, seed_periods[0], "R", "Rent", "100.00")
            _txn(db, seed_user, seed_periods[0], "C", "Car Payment", "100.00")
            db.session.commit()

            report = compute_spending_report(
                seed_user["user"].id, _pp_window(seed_periods[0]),
                user_settings=None,
            )
            tied = [
                row.group_name for row in report.breakdown
                if row.amount == Decimal("100.00")
            ]
            assert tied == ["Auto", "Home"], (
                "a tied group ranking must be a function of the data"
            )

    def test_tied_surprises_order_by_id_so_the_CAP_is_deterministic(
        self, app, seed_user, seed_periods, db,
    ):
        """The five shown surprises are chosen by the data, not the plan (**P74**).

        This is the worst of the three unstable ranks because a CAP follows it:
        keyed on ``abs(delta)`` alone, two rows that missed their estimate by
        the same amount were separated only by the order the query happened to
        return them in, so at the boundary the DATABASE decided which row is on
        the screen rather than merely in what order.

        **The reducer is fed a REVERSED list on purpose, and the first version
        of this test did not do that and was a tautology**: driven through
        ``compute_spending_report`` the rows arrive in id order anyway, so a
        stable sort with no tiebreaker produced the same answer as a total one
        and the test passed against the defect it names.  Reversing the input
        is what makes the two orderings distinguishable -- an unstable rank
        drops the LOWEST id, a total one drops the highest.
        """
        with app.app_context():
            for n in range(6):
                _txn(
                    db, seed_user, seed_periods[0], f"S{n}", "Groceries",
                    "50.00", actual="60.00",
                )
            db.session.commit()

            rows = (
                db.session.query(Transaction)
                .filter_by(pay_period_id=seed_periods[0].id)
                .order_by(Transaction.id.desc())
                .all()
            )
            surprises = _build_surprises(
                rows,
                derived_amount_basis(
                    seed_user["user"].id, seed_user["scenario"].id,
                ),
            )

            ids = [row.item_key for row in surprises.rows]
            assert len(ids) == _MAX_SURPRISES, "the cap is five"
            assert ids == sorted(ids), (
                "tied surprises must rank by a total key: fed in reverse, an "
                "unstable rank keeps reverse order and drops the wrong row"
            )
            assert surprises.net == Decimal("60.00"), (
                "the net sums EVERY surprise, not the shown five"
            )


class TestNewMeansTheCategoryWasNotThereBefore:
    """The "new" badge asks PRESENCE, and a zero total is not absence.

    Plan step ``bank_import:X-gj-2b-3``.  All three ``is_new`` fields read
    ``prior_amount == ZERO``, which is a different question from *the prior
    window held no row for this*.  Two reachable shapes total zero while being
    present: an envelope settled with NO purchases contributes ``0`` and is in
    the window's map, and since ruling **bank_import:R-II** so does one whose
    refunds exactly cancelled its purchases.  Both rendered the badge on a
    category that had been there all along.

    The maps' KEYS are what record which categories a window held, so absence
    is read off them.
    """

    def _one(self, amount):
        """Return a one-category map worth *amount*, under a stable label."""
        return {
            7: _CategoryTotal(
                group_name="Food", item_name="Groceries",
                amount=Decimal(amount),
            ),
        }

    def test_a_category_that_broke_even_before_is_not_new(self):
        """Prior window HELD it at ``0.00``; the badge must not render.

        Asserts the delta beside it, because the two read the same prior
        figure and only one of them changed: the delta is still ``100.00``.
        """
        rows = _build_breakdown(self._one("100.00"), self._one("0.00"))

        assert rows[0].is_new is False
        assert rows[0].items[0].is_new is False
        assert rows[0].items[0].delta == Decimal("100.00")

    def test_a_category_absent_before_is_new(self):
        """The control: a category the prior window did not hold at all."""
        rows = _build_breakdown(self._one("100.00"), {})

        assert rows[0].is_new is True
        assert rows[0].items[0].is_new is True

    def test_a_group_whose_only_category_broke_even_before_is_not_new(self):
        """The GROUP arm has its own map and its own defect.

        Its prior side was a ``defaultdict`` summed per group, so an absent
        group and a group that summed to zero were the same value.  A category
        that changed groups is the shape that separates them: the prior window
        held ``Food: Groceries`` at ``0.00``, so the group is not new even
        though this window's category id is different.
        """
        rows = _build_breakdown(
            {8: _CategoryTotal("Food", "Takeout", Decimal("40.00"))},
            self._one("0.00"),
        )

        assert rows[0].group_name == "Food"
        assert rows[0].is_new is False
        # The ITEM is new -- category 8 was not in the prior window -- which is
        # what makes this case grade the group arm rather than the item one.
        assert rows[0].items[0].is_new is True

    def test_a_change_row_for_a_category_that_broke_even_before_is_not_new(
        self,
    ):
        """The By-change lens's own arm, over the same two maps."""
        changes = _build_changes(self._one("100.00"), self._one("0.00"))

        assert len(changes) == 1
        assert changes[0].is_new is False

    def test_a_change_row_that_breaks_even_NOW_is_still_new(self):
        """The half the old spelling got wrong in the other direction.

        ``prior_amount == ZERO and current_amount > ZERO`` withheld the badge
        from a category that appeared this window and netted to zero -- a
        purchase and its refund in one window, which is exactly what ruling
        **bank_import:R-II** files.  The row exists only because one of the two
        windows held it, so the prior window's absence is the whole test.
        """
        changes = _build_changes(self._one("0.00"), {})

        assert len(changes) == 1
        assert changes[0].is_new is True
